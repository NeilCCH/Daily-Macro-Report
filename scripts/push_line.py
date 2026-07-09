#!/usr/bin/env python3
"""Push today's report card image to the LINE groups via pushMessage.

By default only the image card is sent. Passing --text-file additionally
sends a text message, but the standard daily flow is image-only.

Env vars required:
  LINE_CHANNEL_ACCESS_TOKEN
  LINE_GROUP_IDS          comma-separated group IDs

Usage (image only, the default):
  push_line.py --image-url https://raw.githubusercontent.com/.../card.png \\
               --preview-url https://raw.githubusercontent.com/.../card_preview.png
"""
import argparse
import os
import sys
import time

import requests

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


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
    print(f"Pushed to {group_id}: OK", file=sys.stderr)


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

    text = None
    if args.text_file and os.path.exists(args.text_file):
        with open(args.text_file, encoding="utf-8") as f:
            text = f.read().strip()

    errors = []
    for group_id in group_ids:
        try:
            push_to_group(token, group_id, args.image_url, args.preview_url, text)
        except Exception as exc:  # noqa: BLE001 - aggregate and report all failures
            errors.append(str(exc))
        time.sleep(0.5)

    if errors:
        raise SystemExit("\n".join(errors))


if __name__ == "__main__":
    main()
