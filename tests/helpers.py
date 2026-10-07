import copy
import struct
import sys
import zlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def row(label, value, dirn="up", pts="1.0", pct="0.10%", kind="daily_close", md="2026-10-06", source="Test Source"):
    return {"label": label, "value": value, "change_pts": pts, "change_pct": pct, "dir": dirn,
            "source": source, "source_url": "https://example.test/quote", "as_of": md,
            "market_date": md, "quote_kind": kind}


def good_report():
    return copy.deepcopy({
        "report_date": "2026/10/07",
        "us_market_closed": False,
        "sections": {
            "us_market": [row("S&P 500", "7,819", "up", "45.0", "0.58%"),
                          row("NASDAQ", "27,600", "up", "122.5", "0.45%"),
                          row("費半 SOX", "12,000", "up", "30.0", "0.25%")],
            "asia_market": [row("日經 225", "70,684", "up", "737.1", "1.05%"),
                            row("台股加權", "49,823", "up", "110.5", "0.22%"),
                            row("台指期夜盤", "50,051", "down", "31.0", "0.06%", kind="futures_night", md="2026-10-07")],
            "fx": [row("USD / TWD", "31.82", "up", "0.02", "", kind="realtime", md="2026-10-07"),
                   row("USD / JPY", "158.5", "up", "0.3", "", kind="realtime", md="2026-10-07"),
                   row("USD / CNY", "6.71", "down", "0.01", "", kind="realtime", md="2026-10-07"),
                   row("USD / EUR", "0.889", "up", "0.001", "", kind="realtime", md="2026-10-07")],
            "commodity_rate": [row("WTI 原油", "$89.96", "up", "$0.63", "0.7%", kind="realtime", md="2026-10-07"),
                               row("Brent 原油", "$101.22", "up", "$0.85", "0.8%", kind="realtime", md="2026-10-07"),
                               row("黃金", "$4,153", "down", "$16.3", "0.39%", kind="realtime", md="2026-10-07"),
                               row("白銀", "$61.41", "down", "$0.50", "0.81%", kind="realtime", md="2026-10-07"),
                               row("美 10Y 公債", "5.31%", "up", "0.03%", "", kind="daily_yield", md="2026-10-06")],
        },
        "highlights": "AI 買盤延續，美股同創收盤新高。",
        "business_angle": "建議每季檢視一次保單與帳戶配置，看看是否仍符合家庭需求。",
        "caring_note": "寒露將至，記得添件外套。",
        "source_note": "資料來源：測試資料",
    })


def make_png(path: Path, w=8, h=8, pad=0):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path.write_bytes(data + b"\x00" * pad if pad else data)
