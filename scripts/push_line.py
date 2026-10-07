#!/usr/bin/env python3
"""Push today's report card image to the LINE groups via pushMessage.

Only the image card is sent by default (--text-file additionally sends text).

Safety model
------------
* Gate first: the report must pass validate_report.py in --for-push mode (no
  legacy mode), today's Taipei date, images present and decodable, and the
  image URLs must be pinned to a commit SHA for THIS report date.
* Every send carries `X-Line-Retry-Key`, a UUID derived deterministically from
  (report date, target, payload hash). The same logical send therefore always
  uses the same key, even from a different run, so a retry after a timeout or
  a crash cannot create a second message inside LINE's 24h key lifetime
  (LINE answers 409 + x-line-accepted-request-id when it already accepted it).
* Per-target results are persisted in reports/<date>/push_state.json
  (committed by the Routine, because its working directory is disposable).
  The file stores a hash of the group id, never the id itself, and no token.
* Already-accepted targets are skipped on resume; only the rest are retried.
  A send whose first attempt is older than 24h and not confirmed is never
  re-sent automatically ("manual_review").
* If the previous state cannot be queried (--remote-state unknown) nothing is
  sent.

"accepted" means LINE accepted the API request (HTTP 200 / 409 with an
accepted-request id). It does not prove every member received the message or
that the image rendered.

Exit codes: 0 all enabled targets accepted; 1 some target not accepted;
2 gate failed (nothing sent); 3 remote state unknown (nothing sent).

Env: LINE_CHANNEL_ACCESS_TOKEN, LINE_GROUP_IDS (comma separated).
data/line_groups.json can disable groups ({"enabled": false}); ids missing
from the json default to enabled; duplicate ids are sent once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import report_assets
import validate_report

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
GROUPS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "line_groups.json"
RETRY_NAMESPACE = uuid.UUID("5f0c2c52-9a0e-4b6e-8f3a-2a6a7c1d9e11")
RETRY_KEY_LIFETIME = timedelta(hours=24)
MAX_ATTEMPTS = 3
BACKOFF = (1, 2, 4)
TIMEOUT = 30
UTC = timezone.utc

DONE = "accepted"
PERMANENT = {"rejected_permanent", "quota_exceeded", "auth_error"}


def load_group_config(path: Path = GROUPS_CONFIG_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read {path.name}: {type(exc).__name__}; nothing was sent")
    if not isinstance(cfg, dict):
        raise SystemExit(f"{path.name} must be a JSON object; nothing was sent")
    return cfg


def target_id(group_id: str) -> str:
    return hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:12]


def build_messages(image_url: str, preview_url: str, text: str | None) -> list[dict]:
    msgs = [{"type": "image", "originalContentUrl": image_url, "previewImageUrl": preview_url}]
    if text:
        msgs.append({"type": "text", "text": text})
    return msgs


def payload_hash(messages: list[dict]) -> str:
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def retry_key(report_date: str, tid: str, phash: str) -> str:
    return str(uuid.uuid5(RETRY_NAMESPACE, f"{report_date}|{tid}|{phash}"))


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"push state {path.name} is unreadable ({type(exc).__name__}); refusing to guess, nothing was sent")
    if not isinstance(data, dict):
        raise SystemExit(f"push state {path.name} is not an object; nothing was sent")
    return data


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(status: int, headers: dict, body: str) -> tuple[str, str]:
    """Return (kind, request_id) where kind is accepted | retry | quota_exceeded | auth_error | rejected_permanent | unknown."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    if status == 200:
        return "accepted", h.get("x-line-request-id", "")
    if status == 409:
        accepted = h.get("x-line-accepted-request-id")
        return ("accepted", accepted) if accepted else ("unknown", h.get("x-line-request-id", ""))
    if status == 429:
        if "monthly limit" in (body or "").lower():
            return "quota_exceeded", h.get("x-line-request-id", "")
        return "retry", h.get("x-line-request-id", "")
    if status in (401, 403):
        return "auth_error", h.get("x-line-request-id", "")
    if 500 <= status < 600:
        return "retry", h.get("x-line-request-id", "")
    return "rejected_permanent", h.get("x-line-request-id", "")


def _default_post(url, headers, json_body, timeout):
    import requests  # imported lazily so gate/tests work without the dependency
    try:
        r = requests.post(url, headers=headers, json=json_body, timeout=timeout)
    except requests.RequestException:
        raise ConnectionError("network") from None
    return r.status_code, dict(r.headers), r.text


def send_target(*, token, group_id, messages, key, rec, post, sleep, now_fn) -> None:
    """Attempt one target with bounded retries, mutating `rec` in place."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-Line-Retry-Key": key}
    for attempt in range(MAX_ATTEMPTS):
        rec["attempts"] = rec.get("attempts", 0) + 1
        try:
            status, resp_headers, body = post(LINE_PUSH_URL, headers, {"to": group_id, "messages": messages}, TIMEOUT)
        except (ConnectionError, TimeoutError, OSError):
            rec["last_http_status"], rec["last_error_class"] = 0, "network_or_timeout"
            kind = "retry"
        else:
            kind, request_id = classify(status, resp_headers, body)
            rec["last_http_status"] = status
            rec["last_error_class"] = "" if kind == "accepted" else kind
            if request_id:
                rec["request_id"] = request_id
        if kind == "accepted":
            rec["status"], rec["accepted_at"] = DONE, _iso(now_fn())
            return
        if kind in PERMANENT or kind == "rejected_permanent":
            rec["status"] = "quota_exceeded" if kind == "quota_exceeded" else "auth_error" if kind == "auth_error" else "rejected_permanent"
            return
        if kind == "unknown":
            rec["status"] = "manual_review"
            return
        if attempt < MAX_ATTEMPTS - 1:
            sleep(BACKOFF[attempt])
    rec["status"] = "failed_retryable"


def run(*, group_ids, group_config, token, report_path: Path, image_url, preview_url, text,
        state_path: Path, today: date, now_fn=lambda: datetime.now(UTC), post=_default_post,
        sleep=time.sleep, remote_state="ok", retry_permanent=False, log=print) -> int:
    if remote_state != "ok":
        log("remote push state could not be queried (unknown); refusing to send", file=sys.stderr)
        return 3

    # ---- gate ---------------------------------------------------------
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log("gate: report.json unreadable", file=sys.stderr)
        return 2
    dir_date = report_path.parent.name
    issues = validate_report.validate(report, report_dir_date=dir_date, today=today, for_push=True)
    issues += validate_report.validate_images(report_path.parent / "card.png", report_path.parent / "card_preview.png")
    for name, url in (("card", image_url), ("card_preview", preview_url)):
        e = report_assets.check_pinned_url(url, dir_date, name)
        if e:
            issues.append(validate_report.Issue("error", "url", e))
    errors = [i for i in issues if i.level == "error"]
    for i in issues:
        log(f"{i.level.upper():7} {i.path}: {i.msg}", file=sys.stderr)
    if errors:
        log(f"gate failed with {len(errors)} error(s); nothing was sent", file=sys.stderr)
        return 2

    messages = build_messages(image_url, preview_url, text)
    phash = payload_hash(messages)
    state = load_state(state_path)
    if state and state.get("payload_hash") not in (None, phash):
        log("gate: push state exists for a different payload (image URLs/text changed). "
            "Resume with the original image URLs recorded in push_state.json; nothing was sent", file=sys.stderr)
        return 2
    state.setdefault("targets", {})
    state.update({"report_date": dir_date, "payload_hash": phash, "image_url": image_url, "preview_url": preview_url})

    seen, failures = set(), 0
    for group_id in group_ids:
        if group_id in seen:
            continue
        seen.add(group_id)
        meta = group_config.get(group_id, {})
        label = meta.get("name", "unnamed")
        tid = target_id(group_id)
        if meta.get("enabled", True) is False:
            log(f"Skipped {label} [{tid}]: disabled in line_groups.json", file=sys.stderr)
            continue
        rec = state["targets"].setdefault(tid, {"name": label})
        status = rec.get("status")
        if status == DONE:
            log(f"Already accepted {label} [{tid}]; not re-sending", file=sys.stderr)
            continue
        if status == "manual_review":
            log(f"{label} [{tid}]: needs manual review (unconfirmed outcome); not re-sending", file=sys.stderr)
            failures += 1
            continue
        if status in PERMANENT and not retry_permanent:
            log(f"{label} [{tid}]: previously failed permanently ({status}); use --retry-permanent after fixing the cause", file=sys.stderr)
            failures += 1
            continue
        first = rec.get("first_attempt_at")
        if first and now_fn() - datetime.fromisoformat(first.replace("Z", "+00:00")) > RETRY_KEY_LIFETIME:
            rec["status"] = "manual_review"
            log(f"{label} [{tid}]: first attempt is older than 24h and unconfirmed; the retry key expired, manual review required", file=sys.stderr)
            save_state(state_path, state)
            failures += 1
            continue
        key = retry_key(dir_date, tid, phash)
        rec.update({"retry_key": key, "payload_hash": phash, "status": "pending"})
        rec.setdefault("first_attempt_at", _iso(now_fn()))
        save_state(state_path, state)  # persist BEFORE sending so a crash is recoverable
        send_target(token=token, group_id=group_id, messages=messages, key=key, rec=rec,
                    post=post, sleep=sleep, now_fn=now_fn)
        save_state(state_path, state)
        if rec["status"] == DONE:
            log(f"LINE accepted the request for {label} [{tid}] (HTTP {rec['last_http_status']})", file=sys.stderr)
        else:
            failures += 1
            log(f"{label} [{tid}]: not accepted ({rec['status']}, HTTP {rec.get('last_http_status')})", file=sys.stderr)
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-url", required=True)
    ap.add_argument("--preview-url", required=True)
    ap.add_argument("--report", required=True, help="reports/<date>/report.json (gate input)")
    ap.add_argument("--state-file", default=None, help="default: <report dir>/push_state.json")
    ap.add_argument("--text-file", default=None)
    ap.add_argument("--remote-state", choices=("ok", "unknown"), default="ok")
    ap.add_argument("--retry-permanent", action="store_true")
    ap.add_argument("--git-sha", default=None, help="also verify the pinned objects exist in local git")
    a = ap.parse_args(argv)

    text = None
    if a.text_file:
        p = Path(a.text_file)
        if not p.exists():
            print(f"--text-file {a.text_file} does not exist; nothing was sent", file=sys.stderr)
            return 2
        text = p.read_text(encoding="utf-8").strip()

    report_path = Path(a.report)
    if a.git_sha:
        d = report_path.parent.name
        for fn in ("card.png", "card_preview.png"):
            if not report_assets.git_object_exists(a.git_sha, d, fn):
                print(f"git object {a.git_sha[:8]}:reports/{d}/{fn} not found; nothing was sent", file=sys.stderr)
                return 2
        print("git objects exist locally (public readability on raw.githubusercontent.com is NOT verified here)", file=sys.stderr)

    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    group_ids = [g.strip() for g in os.environ["LINE_GROUP_IDS"].split(",") if g.strip()]
    if not group_ids:
        print("LINE_GROUP_IDS is empty", file=sys.stderr)
        return 2
    from zoneinfo import ZoneInfo
    code = run(
        group_ids=group_ids, group_config=load_group_config(), token=token, report_path=report_path,
        image_url=a.image_url, preview_url=a.preview_url, text=text,
        state_path=Path(a.state_file) if a.state_file else report_path.parent / "push_state.json",
        today=datetime.now(ZoneInfo("Asia/Taipei")).date(), remote_state=a.remote_state,
        retry_permanent=a.retry_permanent,
        log=lambda *args, **kw: print(*args, **kw),
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
