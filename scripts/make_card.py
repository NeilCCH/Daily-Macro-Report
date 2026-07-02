#!/usr/bin/env python3
"""Render report.json into a PNG morning-report card (+ a <1MB preview).

Usage: make_card.py path/to/report.json path/to/output.png
Produces output.png and output_preview.png (downscaled, <1MB, used as LINE
previewImageUrl).
"""
import argparse
import glob
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1080
MARGIN = 56
NAVY = (20, 38, 66)
GOLD = (212, 175, 55)
RED_UP = (214, 40, 40)
GREEN_DOWN = (30, 140, 80)
FLAT = (120, 128, 142)
INK = (32, 32, 32)
SUBINK = (90, 90, 90)
WHITE = (255, 255, 255)
BOX_GOLD_BG = (255, 248, 230)
BOX_BLUE_BG = (232, 242, 255)
BOX_PINK_BG = (255, 235, 242)
BOX_GOLD_ACCENT = (212, 175, 55)
BOX_BLUE_ACCENT = (66, 119, 209)
BOX_PINK_ACCENT = (224, 100, 140)

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]

FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]


def find_font(candidates: list[str]) -> str:
    for path in candidates:
        if os.path.exists(path):
            return path
    for pattern in ("/usr/share/fonts/**/*CJK*", "/usr/share/fonts/**/*NotoSans*"):
        matches = sorted(glob.glob(pattern, recursive=True))
        if matches:
            return matches[0]
    raise FileNotFoundError(
        "No CJK font found. Install fonts-noto-cjk (apt-get install -y fonts-noto-cjk) first."
    )


def load_fonts():
    regular_path = find_font(FONT_CANDIDATES)
    try:
        bold_path = find_font(FONT_CANDIDATES_BOLD)
    except FileNotFoundError:
        bold_path = regular_path
    return {
        "title": ImageFont.truetype(bold_path, 52),
        "subtitle": ImageFont.truetype(regular_path, 26),
        "section": ImageFont.truetype(bold_path, 30),
        "row_label": ImageFont.truetype(regular_path, 28),
        "row_value": ImageFont.truetype(bold_path, 28),
        "box_title": ImageFont.truetype(bold_path, 28),
        "box_body": ImageFont.truetype(regular_path, 26),
        "footer": ImageFont.truetype(regular_path, 20),
    }


def row_direction(row):
    """Return 'up' | 'down' | 'flat'. Honors explicit row['dir'], else falls back
    to the legacy boolean row['up']."""
    d = row.get("dir")
    if d in ("up", "down", "flat"):
        return d
    return "up" if row.get("up") else "down"


def draw_rows(draw, fonts, y, rows):
    for row in rows:
        draw.text((MARGIN + 20, y), row["label"], font=fonts["row_label"], fill=INK)
        direction = row_direction(row)
        color = {"up": RED_UP, "down": GREEN_DOWN, "flat": FLAT}[direction]
        arrow = {"up": "▲", "down": "▼", "flat": "→"}[direction]
        value_text = f"{row['value']}  {arrow}{row.get('change_pct', '')}"
        bbox = draw.textbbox((0, 0), value_text, font=fonts["row_value"])
        text_w = bbox[2] - bbox[0]
        draw.text((WIDTH - MARGIN - 20 - text_w, y), value_text, font=fonts["row_value"], fill=color)
        y += 44
    return y


def draw_section(draw, fonts, y, title, rows):
    if not rows:
        return y
    draw.text((MARGIN, y), title, font=fonts["section"], fill=NAVY)
    y += 46
    y = draw_rows(draw, fonts, y, rows)
    return y + 16


def wrap_text(draw, text, font, max_width):
    lines = []
    current = ""
    for ch in text:
        trial = current + ch
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def draw_box(draw, fonts, y, title, body, bg, accent):
    box_w = WIDTH - 2 * MARGIN
    line_height = 34
    lines = wrap_text(draw, body, fonts["box_body"], box_w - 60)
    box_h = 56 + line_height * len(lines) + 20

    draw.rounded_rectangle((MARGIN, y, MARGIN + box_w, y + box_h), radius=18, fill=bg)
    draw.rounded_rectangle((MARGIN, y, MARGIN + 8, y + box_h), radius=4, fill=accent)

    draw.text((MARGIN + 28, y + 18), title, font=fonts["box_title"], fill=accent)
    text_y = y + 18 + 38
    for line in lines:
        draw.text((MARGIN + 28, text_y), line, font=fonts["box_body"], fill=INK)
        text_y += line_height

    return y + box_h + 24


def render(report: dict, out_path: str) -> None:
    fonts = load_fonts()
    # Generous canvas; the final crop trims to the actual content height. Must stay
    # larger than any real card (3 US rows + 5 commodity rows + 3 wrapped text boxes
    # already reach ~1650px), otherwise overflow gets padded black by the crop.
    height_estimate = 2600
    img = Image.new("RGB", (WIDTH, height_estimate), WHITE)
    draw = ImageDraw.Draw(img)

    header_h = 150
    draw.rectangle((0, 0, WIDTH, header_h), fill=NAVY)
    draw.rectangle((0, header_h, WIDTH, header_h + 6), fill=GOLD)

    draw.text((MARGIN, 30), "早安報報", font=fonts["title"], fill=WHITE)
    draw.text((MARGIN, 96), "每日總經速報", font=fonts["subtitle"], fill=GOLD)

    date_text = report.get("report_date", "")
    right_text = f"{date_text}  夥伴早安"
    bbox = draw.textbbox((0, 0), right_text, font=fonts["subtitle"])
    text_w = bbox[2] - bbox[0]
    draw.text((WIDTH - MARGIN - text_w, 100), right_text, font=fonts["subtitle"], fill=WHITE)

    y = header_h + 36
    sections = report.get("sections", {})

    if report.get("us_market_closed"):
        draw.text((MARGIN, y), "美股（今日休市）", font=fonts["section"], fill=NAVY)
        y += 60
    else:
        y = draw_section(draw, fonts, y, "美股/費半", sections.get("us_market", []))

    y = draw_section(draw, fonts, y, "亞股", sections.get("asia_market", []))
    y = draw_section(draw, fonts, y, "匯率", sections.get("fx", []))
    y = draw_section(draw, fonts, y, "原物料/利率", sections.get("commodity_rate", []))

    y += 8
    y = draw_box(draw, fonts, y, "今日重點", report.get("highlights", ""), BOX_GOLD_BG, BOX_GOLD_ACCENT)
    y = draw_box(draw, fonts, y, "今日業務切入點", report.get("business_angle", ""), BOX_BLUE_BG, BOX_BLUE_ACCENT)
    y = draw_box(draw, fonts, y, "貼心小語", report.get("caring_note", ""), BOX_PINK_BG, BOX_PINK_ACCENT)

    # Footer: source line (data-driven) + fixed internal-use disclaimer.
    source_line = report.get(
        "source_note", "資料來源：TheStreet / Yahoo Finance / Reuters"
    )
    footer_lines = [source_line, "本訊息僅供內部參考，不構成投資建議。"]
    y += 12
    for line in footer_lines:
        draw.text((MARGIN, y), line, font=fonts["footer"], fill=SUBINK)
        y += 30
    y += 6

    img = img.crop((0, 0, WIDTH, y))
    img.save(out_path, "PNG", optimize=True)

    preview_path = os.path.splitext(out_path)[0] + "_preview.png"
    make_preview(img, preview_path)


def make_preview(img: Image.Image, preview_path: str, max_bytes: int = 1_000_000) -> None:
    scale = 0.6
    while scale > 0.1:
        w = int(img.width * scale)
        h = int(img.height * scale)
        resized = img.resize((w, h), Image.LANCZOS)
        resized.save(preview_path, "PNG", optimize=True)
        if os.path.getsize(preview_path) <= max_bytes:
            return
        scale -= 0.1
    resized.save(preview_path, "JPEG", quality=80)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_json")
    parser.add_argument("out_png")
    args = parser.parse_args()

    with open(args.report_json, encoding="utf-8") as f:
        report = json.load(f)

    render(report, args.out_png)
    print(f"Wrote {args.out_png}", file=sys.stderr)


if __name__ == "__main__":
    main()
