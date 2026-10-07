#!/usr/bin/env python3
"""Generate line_text.txt from report.json so the text never drifts from the card.

Usage: make_line_text.py reports/<date>/report.json reports/<date>/line_text.txt [--legacy]

Validates first (same checks as the card); nothing is written on errors.
Per the report spec the last line is the 貼心小語 (no trailing disclaimer or
pleasantries); the disclaimer and source live on the card image. The text is
NOT pushed to LINE by default (the daily flow is image-only); it is a repo
record / manual-paste copy.
"""
import json
import re
import sys
from pathlib import Path

import validate_report

SECTIONS = [
    ("us_market", "📈 美股", lambda l: l),
    ("asia_market", "🌏 亞股", lambda l: l),
    ("fx", "💱 匯率", lambda l: l.replace(" / ", "/")),
    ("commodity_rate", "🛢 原物料 / 利率", lambda l: l),
]
SYMBOL = {"up": "🔺", "down": "🔻", "flat": "➡️", "unknown": "▫️"}


def render(report: dict) -> str:
    y, m, d = report["report_date"].split("/")
    lines = [f"【早安報報｜每日總經速報】{y}/{m}/{d}", "夥伴早安 ☀", ""]
    for key, title, fmt in SECTIONS:
        rows = report.get("sections", {}).get(key) or []
        if key == "us_market" and report.get("us_market_closed"):
            lines += [title, "美股休市", ""]
            continue
        if not rows:
            continue
        lines.append(title)
        for r in rows:
            sym = SYMBOL[r["dir"]]
            pts, pct = r.get("change_pts", ""), r.get("change_pct", "")
            if r["dir"] == "flat" and not pts:
                delta = ""
            else:
                delta = pts + (f"（{pct}）" if pct else "") if pts else (f"（{pct}）" if pct else "")
            lines.append(f"{fmt(r['label'])}：{r['value']} {sym}{delta}".rstrip())
        lines.append("")
    lines += ["🔥 今日重點", report["highlights"], "",
              "💼 今日業務切入點", report["business_angle"], "",
              "❤️ 貼心小語", report["caring_note"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--legacy"]
    if len(args) != 2:
        print("usage: make_line_text.py report.json line_text.txt [--legacy]", file=sys.stderr)
        return 2
    src = Path(args[0])
    report = json.loads(src.read_text(encoding="utf-8"))
    dir_date = src.parent.name if re.fullmatch(r"\d{4}-\d{2}-\d{2}", src.parent.name) else None
    issues = validate_report.validate(report, report_dir_date=dir_date, legacy="--legacy" in sys.argv[1:])
    errs = [i for i in issues if i.level == "error"]
    for i in errs:
        print(f"ERROR   {i.path}: {i.msg}", file=sys.stderr)
    if errs:
        return 1
    Path(args[1]).write_text(render(report), encoding="utf-8")
    print(f"wrote {args[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
