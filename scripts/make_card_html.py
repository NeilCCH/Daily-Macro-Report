#!/usr/bin/env python3
"""Render report.json into a self-contained HTML card.

No Pillow / no network needed — designed to be screenshotted to PNG by
headless Chromium (see shot_card.js). Uses the WenQuanYi Zen Hei CJK font
that ships with the base image, so Traditional Chinese renders correctly.

Usage: make_card_html.py path/to/report.json path/to/card.html [--legacy]

The report is validated first (validate_report.py); on any error nothing is
written. --legacy only relaxes provenance checks so OLD reports can still be
re-rendered; it is never accepted by the push gate.
"""
import html
import json
import re
import sys
from pathlib import Path

import validate_report

RED_UP = "#d62828"     # 漲=紅（台股慣例）
GREEN_DOWN = "#1e8c50"  # 跌=綠
FLAT = "#788092"

SECTION_TITLES = {
    "us_market": "美股",
    "asia_market": "亞股",
    "fx": "匯率",
    "commodity_rate": "原物料 / 利率",
}
SECTION_ORDER = ["us_market", "asia_market", "fx", "commodity_rate"]


def arrow(dirn: str) -> tuple[str, str]:
    if dirn == "up":
        return "▲", RED_UP
    if dirn == "down":
        return "▼", GREEN_DOWN
    if dirn == "flat":  # confirmed zero change only
        return "→", FLAT
    if dirn == "unknown":  # comparison value unavailable: show no direction
        return "–", FLAT
    raise ValueError(f"unsupported dir {dirn!r}")


def section_title(key: str, items: list) -> str:
    if key == "us_market" and any("SOX" in str(i.get("label", "")) for i in items):
        return "美股 / 費半"
    return SECTION_TITLES.get(key, key)


def esc(s: str) -> str:
    return html.escape(str(s))


def render(report: dict) -> str:
    date = esc(report.get("report_date", ""))
    closed = report.get("us_market_closed", False)
    sections = report.get("sections", {})

    rows_html = []
    for key in SECTION_ORDER:
        items = sections.get(key) or []
        title = section_title(key, items)
        if key == "us_market" and closed:
            rows_html.append(
                f'<div class="sec"><div class="sec-title">{esc(title)}</div>'
                f'<div class="closed">美股休市</div></div>'
            )
            continue
        if not items:
            continue
        line_items = []
        for it in items:
            ar, col = arrow(it["dir"])
            pts = esc(it.get("change_pts", ""))
            pct = esc(it.get("change_pct", ""))
            delta = " ".join(p for p in (pts, f"({pct})" if pct else "") if p)
            delta_html = f'<span class="pct" style="color:{col}">{delta}</span>'  # empty span keeps columns aligned
            line_items.append(
                f'<div class="row">'
                f'<span class="label">{esc(it.get("label",""))}</span>'
                f'<span class="val">{esc(it.get("value",""))}</span>'
                f'<span class="arr" style="color:{col}">{ar}</span>'
                f'{delta_html}</div>'
            )
        rows_html.append(
            f'<div class="sec"><div class="sec-title">{esc(title)}</div>'
            + "".join(line_items) + "</div>"
        )

    boxes = [
        ("🔥 今日重點", report.get("highlights", ""), "gold"),
        ("💼 今日業務切入點", report.get("business_angle", ""), "blue"),
        ("❤️ 貼心小語", report.get("caring_note", ""), "pink"),
    ]
    boxes_html = "".join(
        f'<div class="box box-{cls}"><div class="box-title">{esc(t)}</div>'
        f'<div class="box-body">{esc(body)}</div></div>'
        for t, body, cls in boxes if body
    )

    foot = esc(report.get("source_note", ""))

    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html,body {{ background:#f4f6f9; }}
  #card {{
    width:1080px; background:#f4f6f9;
    font-family:'WenQuanYi Zen Hei','Noto Sans CJK TC',sans-serif;
    font-weight:600;
    color:#202020;
  }}
  .hdr {{ background:#142642; padding:44px 56px 32px; position:relative; }}
  .hdr:after {{ content:""; position:absolute; left:0; right:0; bottom:0; height:8px; background:#d4af37; }}
  .brand {{ color:#fff; font-size:60px; font-weight:bold; letter-spacing:2px; }}
  .brand-sub {{ color:#d4af37; font-size:30px; margin-top:10px; }}
  .hdr-right {{ position:absolute; top:52px; right:56px; text-align:right; }}
  .hdr-date {{ color:#e0e4ee; font-size:34px; }}
  .hdr-hi {{ color:#c4cede; font-size:26px; margin-top:14px; }}
  .body {{ padding:36px 56px 20px; }}
  .sec {{ margin-bottom:22px; }}
  .sec-title {{ color:#142642; font-size:34px; font-weight:bold;
    border-bottom:3px solid #e4e7ed; padding-bottom:10px; margin-bottom:12px; }}
  .row {{ display:flex; align-items:baseline; padding:7px 8px; font-size:31px; }}
  .label {{ color:#5a5a5a; flex:0 0 260px; }}
  .val {{ color:#202020; flex:1; text-align:right; font-weight:bold; }}
  .arr {{ width:52px; text-align:right; font-size:29px; }}
  .pct {{ width:230px; text-align:right; font-size:26px; white-space:nowrap; }}
  .closed {{ color:#788092; font-size:30px; padding:8px; }}
  .box {{ border-radius:20px; padding:26px 30px; margin:0 0 22px; border-left:12px solid; }}
  .box-title {{ font-size:32px; font-weight:bold; margin-bottom:14px; }}
  .box-body {{ font-size:29px; line-height:1.55; color:#2a2a2a; }}
  .box-gold {{ background:#fff8e6; border-color:#d4af37; }}
  .box-gold .box-title {{ color:#b0791c; }}
  .box-blue {{ background:#e8f2ff; border-color:#4277d1; }}
  .box-blue .box-title {{ color:#1c4494; }}
  .box-pink {{ background:#ffebf2; border-color:#e0648c; }}
  .box-pink .box-title {{ color:#c0295a; }}
  .foot {{ border-top:2px solid #e4e7ed; margin:6px 56px 0; padding:22px 0 34px;
    color:#8a8a8a; font-size:22px; line-height:1.5; }}
</style></head>
<body><div id="card">
  <div class="hdr">
    <div class="brand">早安報報</div>
    <div class="brand-sub">每日總經速報</div>
    <div class="hdr-right"><div class="hdr-date">{date}</div><div class="hdr-hi">夥伴早安 ☀</div></div>
  </div>
  <div class="body">{''.join(rows_html)}{boxes_html}</div>
  <div class="foot">{foot}<br>本訊息僅供內部參考，不構成投資建議。</div>
</div></body></html>"""


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--legacy"]
    legacy = "--legacy" in sys.argv[1:]
    if len(args) != 2:
        print("usage: make_card_html.py report.json card.html [--legacy]", file=sys.stderr)
        return 2
    src = Path(args[0])
    with open(src, encoding="utf-8") as f:
        report = json.load(f)
    dir_date = src.parent.name if re.fullmatch(r"\d{4}-\d{2}-\d{2}", src.parent.name) else None
    issues = validate_report.validate(report, report_dir_date=dir_date, legacy=legacy)
    for i in issues:
        print(f"{i.level.upper():7} {i.path}: {i.msg}", file=sys.stderr)
    if any(i.level == "error" for i in issues):
        print("make_card_html: report failed validation; no card written", file=sys.stderr)
        return 1
    with open(args[1], "w", encoding="utf-8") as f:
        f.write(render(report))
    print(f"wrote {args[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
