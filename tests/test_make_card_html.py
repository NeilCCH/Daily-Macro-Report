import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
import make_card_html as mc
from helpers import good_report


class Card(unittest.TestCase):
    def test_arrows(self):
        self.assertEqual(mc.arrow("up")[0], "▲")
        self.assertEqual(mc.arrow("down")[0], "▼")
        self.assertEqual(mc.arrow("flat")[0], "→")
        self.assertEqual(mc.arrow("unknown")[0], "–")
        for bad in ("invalid", None, "", "UP"):
            with self.assertRaises(ValueError):
                mc.arrow(bad)

    def test_render_good_report(self):
        html = mc.render(good_report())
        self.assertIn("美股 / 費半", html)
        self.assertIn("2026/10/07", html)

    def test_title_drops_sox_when_row_omitted(self):
        r = good_report(); r["sections"]["us_market"].pop()
        self.assertIn(">美股<", mc.render(r))
        self.assertNotIn("費半", mc.render(r).split("<style>")[0].split("</style>")[-1])

    def test_html_is_escaped(self):
        r = good_report(); r["highlights"] = "<script>alert(1)</script>"
        self.assertNotIn("<script>alert", mc.render(r))

    def test_empty_delta_keeps_column_alignment(self):
        r = good_report(); r["sections"]["fx"][0].update(change_pts="", change_pct="", dir="unknown")
        html = mc.render(r)
        self.assertEqual(html.count('class="row"'), html.count('class="pct"'))

    def test_main_refuses_invalid_report_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            day = Path(d) / "2026-10-07"; day.mkdir()
            r = good_report(); r["sections"]["fx"][0]["value"] = "NaN"; r["sections"]["fx"][0]["dir"] = "invalid"
            (day / "report.json").write_text(json.dumps(r), encoding="utf-8")
            out = day / "card.html"
            with mock.patch("sys.argv", ["x", str(day / "report.json"), str(out)]):
                self.assertEqual(mc.main(), 1)
            self.assertFalse(out.exists())

    def test_main_renders_valid_report(self):
        with tempfile.TemporaryDirectory() as d:
            day = Path(d) / "2026-10-07"; day.mkdir()
            (day / "report.json").write_text(json.dumps(good_report()), encoding="utf-8")
            with mock.patch("sys.argv", ["x", str(day / "report.json"), str(day / "card.html")]):
                self.assertEqual(mc.main(), 0)
            self.assertTrue((day / "card.html").exists())

    def test_legacy_flag_renders_old_report_without_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            day = Path(d) / "2026-10-07"; day.mkdir()
            r = good_report()
            for sec in r["sections"].values():
                for x in sec:
                    for k in ("source", "source_url", "as_of", "market_date", "quote_kind"):
                        x.pop(k, None)
            (day / "report.json").write_text(json.dumps(r), encoding="utf-8")
            with mock.patch("sys.argv", ["x", str(day / "report.json"), str(day / "a.html")]):
                self.assertEqual(mc.main(), 1)
            with mock.patch("sys.argv", ["x", str(day / "report.json"), str(day / "a.html"), "--legacy"]):
                self.assertEqual(mc.main(), 0)


if __name__ == "__main__":
    unittest.main()
