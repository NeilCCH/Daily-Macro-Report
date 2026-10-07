#!/usr/bin/env python3
"""Validate a daily report (report.json + card images) before rendering/pushing.

Usage:
  validate_report.py reports/2026-10-07/report.json [--images card.png card_preview.png]
                     [--for-push] [--legacy] [--today YYYY-MM-DD]

Exit 0 when there are no errors, 1 otherwise. Warnings never fail the run.

--legacy relaxes the provenance requirements so OLD reports can still be read
and rendered. It is deliberately incompatible with --for-push: a legacy-mode
pass can never authorise a real LINE push.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import quote_freshness as qf

VALID_DIRS = {"up", "down", "flat", "unknown"}
SECTION_KEYS = ("us_market", "asia_market", "fx", "commodity_rate")
BANNED = ("保證", "一定", "穩賺", "最佳時機", "不會賠")
KINDS = {"realtime", "daily_close", "daily_yield", "futures_night"}
REQUIRED_ROWS = {
    "us_market": ["S&P 500", "NASDAQ", "費半 SOX"],
    "asia_market": ["日經 225", "台股加權", "台指期夜盤"],
    "fx": ["USD / TWD", "USD / JPY", "USD / CNY", "USD / EUR"],
    "commodity_rate": ["WTI 原油", "Brent 原油", "黃金", "白銀", "美 10Y 公債"],
}
LINE_ORIGINAL_MAX = 10 * 1024 * 1024  # LINE image message: original <= 10 MB
LINE_PREVIEW_MAX = 1 * 1024 * 1024    # preview <= 1 MB
TAIPEI = ZoneInfo("Asia/Taipei")


class Issue:
    def __init__(self, level: str, path: str, msg: str):
        self.level, self.path, self.msg = level, path, msg

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.level.upper()} {self.path}: {self.msg}"


def _num(s) -> float | None:
    if isinstance(s, bool) or not isinstance(s, str):
        return None
    t = s.strip().replace(",", "").replace("$", "").replace("%", "")
    if not t:
        return None
    try:
        f = float(t)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _decimals(s: str) -> int:
    t = s.strip().replace("%", "").replace("$", "").replace(",", "")
    return len(t.split(".")[1]) if "." in t else 0


def _date_or_none(s) -> date | None:
    try:
        return date.fromisoformat(s) if isinstance(s, str) and len(s) == 10 else None
    except ValueError:
        return None


def expected_market_dates(section: str, kind: str | None, report_day: date) -> set[date]:
    prev = qf.prev_weekday(report_day)
    if section == "us_market":
        return {prev}
    return {report_day, prev}


def validate(report, *, report_dir_date: str | None = None, today: date | None = None,
             legacy: bool = False, for_push: bool = False) -> list[Issue]:
    issues: list[Issue] = []

    def err(path, msg): issues.append(Issue("error", path, msg))
    def warn(path, msg): issues.append(Issue("warning", path, msg))

    if legacy and for_push:
        err("mode", "--legacy cannot be combined with --for-push; legacy mode never authorises a real push")
        return issues
    if not isinstance(report, dict):
        err("$", "report must be a JSON object")
        return issues

    # ---- date -------------------------------------------------------
    rd = report.get("report_date")
    report_day = None
    if not isinstance(rd, str) or not re.fullmatch(r"\d{4}/\d{2}/\d{2}", rd):
        err("report_date", "must be a string like YYYY/MM/DD")
    else:
        try:
            report_day = date(*map(int, rd.split("/")))
        except ValueError:
            err("report_date", f"{rd} is not a real calendar date")
    if report_day and report_dir_date and report_day.isoformat() != report_dir_date:
        err("report_date", f"{rd} does not match the output directory date {report_dir_date}")
    if report_day and today and for_push and report_day != today:
        err("report_date", f"{rd} is not today's Taipei date {today.isoformat()}; refusing to push a stale report")

    closed = report.get("us_market_closed")
    if not isinstance(closed, bool):
        err("us_market_closed", "must be true or false")
        closed = False

    # ---- sections ---------------------------------------------------
    sections = report.get("sections")
    if not isinstance(sections, dict):
        err("sections", "must be an object")
        sections = {}
    for k in sections:
        if k not in SECTION_KEYS:
            warn(f"sections.{k}", "unknown section is ignored by the renderer")

    present: dict[str, set[str]] = {k: set() for k in SECTION_KEYS}
    for sec in SECTION_KEYS:
        rows = sections.get(sec, [])
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            err(f"sections.{sec}", "must be a list")
            continue
        if sec == "us_market" and closed and rows:
            err("us_market_closed", "marked closed but us_market still has rows (do not reuse old data)")
        for i, row in enumerate(rows):
            p = f"sections.{sec}[{i}]"
            if not isinstance(row, dict):
                err(p, "row must be an object")
                continue
            label = row.get("label")
            if not isinstance(label, str) or not label.strip():
                err(f"{p}.label", "missing label")
                label = f"#{i}"
            present[sec].add(label)
            _check_row(p, label, sec, row, report_day, legacy, err, warn)

    omitted = report.get("omitted", [])
    omitted_labels = set()
    if omitted is not None:
        if not isinstance(omitted, list):
            err("omitted", "must be a list of {label, reason}")
        else:
            for j, o in enumerate(omitted):
                if isinstance(o, dict) and isinstance(o.get("label"), str) and isinstance(o.get("reason"), str) and o["reason"].strip():
                    omitted_labels.add(o["label"])
                else:
                    err(f"omitted[{j}]", "needs a label and a non-empty reason")
    for sec, labels in REQUIRED_ROWS.items():
        if sec == "us_market" and closed:
            continue
        for lb in labels:
            if lb not in present[sec] and lb not in omitted_labels:
                (warn if legacy else err)(f"sections.{sec}", f"'{lb}' is missing and no omitted[] reason was recorded")

    # ---- text blocks + compliance ----------------------------------
    for key in ("highlights", "business_angle", "caring_note", "source_note"):
        v = report.get(key)
        if not isinstance(v, str) or not v.strip():
            err(key, "must be a non-empty string")
            continue
        for w in BANNED:
            if w in v:
                err(key, f"contains banned wording '{w}'")
    for j, o in enumerate(omitted if isinstance(omitted, list) else []):
        if isinstance(o, dict) and isinstance(o.get("reason"), str):
            for w in BANNED:
                if w in o["reason"]:
                    err(f"omitted[{j}]", f"contains banned wording '{w}'")
    return issues


def _check_row(p, label, sec, row, report_day, legacy, err, warn):
    value = row.get("value")
    if not isinstance(value, str) or _num(value) is None:
        err(f"{p}.value", f"'{value}' is not a finite number string")
    dirn = row.get("dir")
    if dirn not in VALID_DIRS:
        err(f"{p}.dir", f"'{dirn}' is not one of up/down/flat/unknown")
    elif dirn == "unknown":
        warn(f"{p}.dir", "direction is unknown (no comparison value); confirm or drop the arrow")

    pts, pct = row.get("change_pts", ""), row.get("change_pct", "")
    for name, v in (("change_pts", pts), ("change_pct", pct)):
        if not isinstance(v, str):
            err(f"{p}.{name}", "must be a string ('' when unknown)")
        elif v != "" and _num(v) is None:
            err(f"{p}.{name}", f"'{v}' is not a finite number string")
    pts_n = _num(pts) if isinstance(pts, str) else None
    pct_n = _num(pct) if isinstance(pct, str) else None
    if pts_n is not None and pts_n < 0:
        err(f"{p}.change_pts", "magnitude only; the sign belongs in dir")
    if dirn in ("up", "down"):
        if pts_n == 0 and (pct_n in (None, 0)):
            err(f"{p}.dir", f"change is zero but dir is '{dirn}' (should be flat)")
    if dirn == "flat":
        if (pts_n not in (None, 0)) or (pct_n not in (None, 0)):
            err(f"{p}.dir", "dir is flat but a non-zero change is shown")
        if pts_n is None and pct_n is None:
            warn(f"{p}.dir", "flat with no explicit zero change; flat must mean a confirmed zero change")

    # pct vs pts/value consistency (price-like rows only: not FX, not yields)
    base_ok = sec in ("us_market", "asia_market") or (sec == "commodity_rate" and "公債" not in label)
    v_n = _num(value) if isinstance(value, str) else None
    if base_ok and dirn in ("up", "down") and None not in (v_n, pts_n, pct_n) and pts_n and v_n:
        prev = v_n - pts_n if dirn == "up" else v_n + pts_n
        if prev > 0:
            calc = pts_n / prev * 100
            tol = (0.5 * 10 ** -_decimals(pct) + calc * (0.5 * 10 ** -_decimals(pts)) / pts_n
                   + calc * 0.5 * 10 ** -_decimals(value) / v_n + 0.01)
            if abs(calc - pct_n) > tol:
                err(f"{p}.change_pct", f"{pct} does not match {pts} on {value} (computed {calc:.2f}%)")

    # provenance
    src, kind, md = row.get("source"), row.get("quote_kind"), row.get("market_date")
    level = warn if legacy else err
    if not isinstance(src, str) or not src.strip():
        level(f"{p}.source", "missing source")
    if kind not in KINDS:
        level(f"{p}.quote_kind", f"'{kind}' must be one of {sorted(KINDS)}")
    md_d = _date_or_none(md)
    if md_d is None:
        level(f"{p}.market_date", "missing or not YYYY-MM-DD")
    elif report_day and not legacy:
        if kind == "daily_yield":
            lag = qf.business_days_between(md_d, qf.prev_weekday(report_day))
            if md_d > report_day or lag > qf.YIELD_MAX_LAG_BUSINESS_DAYS:
                err(f"{p}.market_date", f"{md} is stale for a {report_day.isoformat()} report")
        else:
            exp = expected_market_dates(sec, kind, report_day)
            if md_d not in exp:
                err(f"{p}.market_date", f"{md} not in expected {sorted(d.isoformat() for d in exp)}")
    if not legacy and not (isinstance(row.get("as_of"), str) and row["as_of"].strip()):
        warn(f"{p}.as_of", "no as_of timestamp recorded")
    url = row.get("source_url")
    if isinstance(url, str) and re.search(r"(api[_-]?key|token|apikey)=", url, re.I):
        err(f"{p}.source_url", "URL appears to contain a credential; strip it")


# ---------------------------------------------------------------------
# images
# ---------------------------------------------------------------------

def inspect_png(path: Path) -> tuple[int, int]:
    """Fully verify a PNG (signature, chunk CRCs, IDAT inflates) and return (w, h)."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG (bad signature)")
    pos, idat, w = 8, bytearray(), None
    saw_end = False
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        crc = data[pos + 8 + length:pos + 12 + length]
        if len(body) != length or len(crc) != 4:
            raise ValueError("truncated chunk")
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != struct.unpack(">I", crc)[0]:
            raise ValueError(f"CRC mismatch in {ctype!r}")
        if ctype == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            saw_end = True
            break
        pos += 12 + length
    if w is None or not saw_end:
        raise ValueError("missing IHDR or IEND")
    zlib.decompress(bytes(idat))
    if w <= 0 or h <= 0:
        raise ValueError("zero-sized image")
    return w, h


def validate_images(image: Path, preview: Path) -> list[Issue]:
    issues = []
    for path, limit, name in ((image, LINE_ORIGINAL_MAX, "card.png"), (preview, LINE_PREVIEW_MAX, "card_preview.png")):
        if not path.exists():
            issues.append(Issue("error", name, f"{path} does not exist"))
            continue
        size = path.stat().st_size
        if size > limit:
            issues.append(Issue("error", name, f"{size} bytes exceeds the {limit} byte limit"))
        try:
            inspect_png(path)
        except (ValueError, zlib.error, struct.error) as exc:
            issues.append(Issue("error", name, f"cannot be decoded: {exc}"))
    return issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--images", nargs=2, metavar=("CARD", "PREVIEW"))
    ap.add_argument("--for-push", action="store_true")
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--today", default=None, help="override Taipei today (tests)")
    a = ap.parse_args(argv)
    path = Path(a.report)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR cannot read report: {type(exc).__name__}", file=sys.stderr)
        return 1
    dir_date = path.parent.name if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.parent.name) else None
    today = date.fromisoformat(a.today) if a.today else datetime.now(TAIPEI).date()
    issues = validate(report, report_dir_date=dir_date, today=today, legacy=a.legacy, for_push=a.for_push)
    if a.images:
        issues += validate_images(Path(a.images[0]), Path(a.images[1]))
    for i in issues:
        print(f"{i.level.upper():7} {i.path}: {i.msg}", file=sys.stderr)
    errors = [i for i in issues if i.level == "error"]
    print(f"validate_report: {len(errors)} error(s), {len(issues) - len(errors)} warning(s)", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
