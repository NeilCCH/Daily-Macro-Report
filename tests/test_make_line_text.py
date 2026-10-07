import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
import make_line_text as mt
from helpers import good_report


class LineText(unittest.TestCase):
    def test_matches_report_row_for_row(self):
        r = good_report()
        text = mt.render(r)
        self.assertTrue(text.startswith("【早安報報｜每日總經速報】2026/10/07\n夥伴早安 ☀"))
        self.assertIn("S&P 500：7,819 🔺45.0（0.58%）", text)
        self.assertIn("台指期夜盤：50,051 🔻31.0（0.06%）", text)
        self.assertIn("USD/TWD：31.82 🔺0.02", text)       # fx labels use the compact form
        self.assertIn("美 10Y 公債：5.31% 🔺0.03%", text)
        for sec in r["sections"].values():
            for x in sec:
                self.assertIn(x["value"], text)

    def test_flat_and_unknown_symbols_and_last_line(self):
        r = good_report()
        r["sections"]["fx"][0].update(dir="flat", change_pts="0.00", change_pct="")
        r["sections"]["fx"][1].update(dir="unknown", change_pts="", change_pct="")
        text = mt.render(r)
        self.assertIn("USD/TWD：31.82 ➡️0.00", text)
        self.assertIn("USD/JPY：158.5 ▫️", text)
        self.assertTrue(text.rstrip().endswith(r["caring_note"]))  # spec: 貼心小語 is the last line
        self.assertNotIn("USD/JPY：158.5 ▫️ ", text)

    def test_omitted_rows_do_not_leave_blank_lines_or_empty_sections(self):
        r = good_report(); r["sections"]["asia_market"] = []
        r["omitted"] = [{"label": n, "reason": "查無資料"} for n in ("日經 225", "台股加權", "台指期夜盤")]
        text = mt.render(r)
        self.assertNotIn("🌏 亞股", text)
        self.assertNotIn("\n\n\n", text)

    def test_closed_us_market(self):
        r = good_report(); r["us_market_closed"] = True; r["sections"]["us_market"] = []
        self.assertIn("美股休市", mt.render(r))

    def test_main_refuses_invalid_report(self):
        with tempfile.TemporaryDirectory() as d:
            day = Path(d) / "2026-10-07"; day.mkdir()
            r = good_report(); r["sections"]["fx"][0]["dir"] = "invalid"
            (day / "report.json").write_text(json.dumps(r), encoding="utf-8")
            with mock.patch("sys.argv", ["x", str(day / "report.json"), str(day / "line_text.txt")]):
                self.assertEqual(mt.main(), 1)
            self.assertFalse((day / "line_text.txt").exists())

    def test_main_writes_valid(self):
        with tempfile.TemporaryDirectory() as d:
            day = Path(d) / "2026-10-07"; day.mkdir()
            (day / "report.json").write_text(json.dumps(good_report()), encoding="utf-8")
            with mock.patch("sys.argv", ["x", str(day / "report.json"), str(day / "line_text.txt")]):
                self.assertEqual(mt.main(), 0)
            self.assertIn("今日重點", (day / "line_text.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
