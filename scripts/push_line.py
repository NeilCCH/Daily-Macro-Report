#!/usr/bin/env python3
"""Push today's report card image to the LINE groups via pushMessage.

By default only the image card is sent. Passing --text-file additionally
sends a text message, but the standard daily flow is image-only.

Env vars required:
  LINE_CHANNEL_ACCESS_TOKEN
  LINE_GROUP_IDS          comma-separated group IDs (the full known list)

Which groups actually receive today's push is filtered through
data/line_groups.json (group_id -> {name, enabled}). To stop sending to a
group, set its "enabled" to false there and merge to the default branch --
no need to touch the LINE_GROUP_IDS environment variable (which also avoids
the "env var only applies to new sessions" gotcha). Group IDs present in
LINE_GROUP_IDS but missing from the json default to enabled.

Usage (image only, the default):
  push_line.py --image-url https://raw.githubusercontent.com/.../card.png \\
               --preview-url https://raw.githubusercontent.com/.../card_preview.png
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
GROUPS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "line_groups.json"


def load_group_config() -> dict:
    if not GROUPS_CONFIG_PATH.exists():
        return {}
    with open(GROUPS_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def push_to_group(token: str, group_id: str, image_url: str, preview_url: str, text: str | None) -> None:
    messages = [
        {
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": preview_url,
        }
    ]
    if text:
        messages.append({"type": "text", "text": text})

    resp = requests.post(
        LINE_PUSH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"to": group_id, "messages": messages},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"LINE push to {group_id} failed: {resp.status_code} {resp.text}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-url", required=True)
    parser.add_argument("--preview-url", required=True)
    parser.add_argument("--text-file", default=None)
    args = parser.parse_args()

    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    group_ids = [g.strip() for g in os.environ["LINE_GROUP_IDS"].split(",") if g.strip()]
    if not group_ids:
        raise SystemExit("LINE_GROUP_IDS is empty")

    group_config = load_group_config()

    text = None
    if args.text_file and os.path.exists(args.text_file):
        with open(args.text_file, encoding="utf-8") as f:
            text = f.read().strip()

    errors = []
    for group_id in group_ids:
        meta = group_config.get(group_id, {})
        label = meta.get("name", group_id)
        if meta.get("enabled", True) is False:
            print(f"Skipped {label} ({group_id}): disabled in line_groups.json", file=sys.stderr)
            continue
        try:
            push_to_group(token, group_id, args.image_url, args.preview_url, text)
            print(f"Pushed to {label} ({group_id}): OK", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - aggregate and report all failures
            errors.append(f"{label} ({group_id}): {exc}")
        time.sleep(0.5)

    if errors:
        raise SystemExit("\n".join(errors))


if __name__ == "__main__":
    main()
