#!/usr/bin/env python3
"""Decide whether today (Asia/Taipei) is a Taiwan workday.

Reads data/taiwan_calendar_2026.json (official calendar incl. holidays and
makeup workdays). Exits 0 + prints "true"/"false" so the workflow can gate
later steps with `if: steps.workday.outputs.is_workday == 'true'`.
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    now_taipei = datetime.now(ZoneInfo("Asia/Taipei"))
    today_str = now_taipei.strftime("%Y%m%d")
    year = now_taipei.strftime("%Y")

    calendar_path = os.path.join(REPO_ROOT, "data", f"taiwan_calendar_{year}.json")
    if not os.path.exists(calendar_path):
        print(f"::warning::No calendar file for {year} at {calendar_path}, falling back to weekday check", file=sys.stderr)
        is_workday = now_taipei.weekday() < 5
    else:
        with open(calendar_path, encoding="utf-8") as f:
            calendar = json.load(f)
        entry = next((e for e in calendar if e["date"] == today_str), None)
        if entry is None:
            print(f"::warning::No calendar entry for {today_str}, falling back to weekday check", file=sys.stderr)
            is_workday = now_taipei.weekday() < 5
        else:
            is_workday = not entry["isHoliday"]
            print(f"{today_str} ({entry['week']}): {entry.get('description') or '一般工作日'}", file=sys.stderr)

    print(f"Taipei date {today_str} is_workday={is_workday}", file=sys.stderr)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"is_workday={'true' if is_workday else 'false'}\n")
            f.write(f"report_date={now_taipei.strftime('%Y-%m-%d')}\n")
    else:
        print("true" if is_workday else "false")


if __name__ == "__main__":
    main()
