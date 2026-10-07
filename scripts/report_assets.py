"""Public image URLs for a report, pinned to a commit SHA (never a branch).

A branch URL can move or disappear (the daily work branches are one-off), which
would break the image in LINE messages that were already sent. A full 40-hex
commit SHA keeps pointing at the exact bytes that were pushed.

"Git object exists" (git_object_exists) is a local fact; "publicly readable on
raw.githubusercontent.com" can only be verified from a network that may reach
that domain (the Routine sandbox cannot), so the two are reported separately.
"""
from __future__ import annotations

import re
import subprocess

RAW_BASE = "https://raw.githubusercontent.com/NeilCCH/Daily-Macro-Report"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL_RE = re.compile(
    r"^https://raw\.githubusercontent\.com/[^/]+/[^/]+/(?P<sha>[0-9a-f]{40})/reports/(?P<date>\d{4}-\d{2}-\d{2})/(?P<name>card|card_preview)\.png$"
)


def build_urls(sha: str, report_date: str) -> dict[str, str]:
    """report_date is YYYY-MM-DD (the reports/ directory name)."""
    if not SHA_RE.match(sha or ""):
        raise ValueError("commit SHA must be the full 40-character lowercase hex (not a branch name or short SHA)")
    if not DATE_RE.match(report_date or ""):
        raise ValueError("report_date must look like YYYY-MM-DD")
    base = f"{RAW_BASE}/{sha}/reports/{report_date}"
    return {"image_url": f"{base}/card.png", "preview_url": f"{base}/card_preview.png"}


def check_pinned_url(url: str, report_date: str, expected_name: str) -> str | None:
    """Return an error string if `url` is not a SHA-pinned URL for this report."""
    m = URL_RE.match(url or "")
    if not m:
        return f"{expected_name}: URL is not a SHA-pinned raw.githubusercontent.com report URL"
    if m["date"] != report_date:
        return f"{expected_name}: URL date {m['date']} != report date {report_date}"
    if m["name"] != expected_name:
        return f"{expected_name}: URL points at {m['name']}.png"
    return None


def git_object_exists(sha: str, report_date: str, filename: str, repo_dir: str | None = None) -> bool:
    """True if <sha>:reports/<date>/<filename> exists in the local git object store."""
    try:
        r = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}:reports/{report_date}/{filename}"],
            cwd=repo_dir, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0
