import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import helpers
import validate_report as vr
from helpers import good_report, make_png, row


def errors(issues):
    return [i for i in issues if i.level == "error"]


def paths(issues):
    return [i.path for i in errors(issues)]


class Validate(unittest.TestCase):
    def v(self, report, **kw):
        kw.setdefault("report_dir_date", "2026-10-07")
        return vr.validate(report, **kw)

    def test_good_report_passes_for_push(self):
        self.assertEqual(errors(self.v(good_report(), today=date(2026, 10, 7), for_push=True)), [])

    def test_missing_date_and_wrong_type(self):
        r = good_report(); del r["report_date"]
        self.assertIn("report_date", paths(self.v(r)))
        r = good_report(); r["report_date"] = "2026-10-07"
        self.assertIn("report_date", paths(self.v(r)))
        r = good_report(); r["report_date"] = "2026/13/45"
        self.assertIn("report_date", paths(self.v(r)))

    def test_date_must_match_directory(self):
        r = good_report(); r["report_date"] = "2026/10/06"
        self.assertTrue(any("directory" in i.msg for i in errors(self.v(r))))

    def test_stale_report_rejected_for_push(self):
        issues = self.v(good_report(), today=date(2026, 10, 8), for_push=True)
        self.assertTrue(any("not today" in i.msg for i in errors(issues)))

    def test_invalid_dir_is_an_error_not_flat(self):
        r = good_report(); r["sections"]["us_market"][0]["dir"] = "invalid"
        self.assertTrue(any(i.path.endswith(".dir") for i in errors(self.v(r))))
        r["sections"]["us_market"][0]["dir"] = None
        self.assertTrue(any(i.path.endswith(".dir") for i in errors(self.v(r))))

    def test_nan_and_garbage_values(self):
        for bad in ("NaN", "inf", "", "abc", None, 5):
            r = good_report(); r["sections"]["fx"][0]["value"] = bad
            self.assertTrue(any(i.path.endswith(".value") for i in errors(self.v(r))), bad)
        r = good_report(); r["sections"]["fx"][0]["change_pts"] = "NaN"
        self.assertTrue(any(i.path.endswith("change_pts") for i in errors(self.v(r))))

    def test_zero_change_marked_down_is_error(self):
        r = good_report(); r["sections"]["fx"][2].update(change_pts="0.00", dir="down")
        self.assertTrue(any("zero" in i.msg for i in errors(self.v(r))))

    def test_flat_with_nonzero_change_is_error(self):
        r = good_report(); r["sections"]["fx"][0]["dir"] = "flat"
        self.assertTrue(any("flat" in i.msg for i in errors(self.v(r))))

    def test_unknown_direction_is_allowed_with_warning(self):
        r = good_report(); r["sections"]["fx"][0].update(dir="unknown", change_pts="", change_pct="")
        issues = self.v(r)
        self.assertEqual(errors(issues), [])
        self.assertTrue(any(i.level == "warning" and "unknown" in i.msg for i in issues))

    def test_pct_consistency_with_rounding_tolerance(self):
        self.assertEqual(errors(self.v(good_report())), [])
        r = good_report(); r["sections"]["us_market"][0]["change_pct"] = "1.58%"
        self.assertTrue(any("does not match" in i.msg for i in errors(self.v(r))))
        r = good_report(); r["sections"]["us_market"][0]["dir"] = "down"  # same numbers, opposite base -> still tiny diff
        self.assertEqual([i for i in errors(self.v(r)) if "does not match" in i.msg], [])

    def test_negative_magnitude_rejected(self):
        r = good_report(); r["sections"]["fx"][0]["change_pts"] = "-0.02"
        self.assertTrue(any("magnitude" in i.msg for i in errors(self.v(r))))

    def test_closed_market_contradiction(self):
        r = good_report(); r["us_market_closed"] = True
        self.assertTrue(any(i.path == "us_market_closed" for i in errors(self.v(r))))
        r["sections"]["us_market"] = []
        self.assertEqual(errors(self.v(r)), [])
        r["us_market_closed"] = "yes"
        self.assertIn("us_market_closed", paths(self.v(r)))

    def test_missing_required_row_needs_reason(self):
        r = good_report(); r["sections"]["us_market"].pop()
        self.assertTrue(any("費半 SOX" in i.msg for i in errors(self.v(r))))
        r["omitted"] = [{"label": "費半 SOX", "reason": "兩次搜尋仍查無可靠收盤"}]
        self.assertEqual(errors(self.v(r)), [])
        r["omitted"] = [{"label": "費半 SOX", "reason": " "}]
        self.assertTrue(errors(self.v(r)))

    def test_banned_words(self):
        for key in ("highlights", "business_angle", "caring_note", "source_note"):
            for w in vr.BANNED:
                r = good_report(); r[key] = f"這個產品{w}好用"
                self.assertTrue(any(i.path == key for i in errors(self.v(r))), (key, w))

    def test_empty_text_blocks(self):
        r = good_report(); r["business_angle"] = ""
        self.assertIn("business_angle", paths(self.v(r)))

    def test_provenance_required_unless_legacy(self):
        r = good_report(); del r["sections"]["fx"][0]["source"]; del r["sections"]["fx"][0]["quote_kind"]
        self.assertTrue(errors(self.v(r)))
        self.assertEqual(errors(self.v(r, legacy=True)), [])

    def test_legacy_mode_cannot_authorise_push(self):
        issues = self.v(good_report(), legacy=True, for_push=True, today=date(2026, 10, 7))
        self.assertTrue(any("legacy" in i.msg for i in errors(issues)))

    def test_old_report_one_week_ago_fails_current_dates(self):
        r = good_report()
        for sec in r["sections"].values():
            for x in sec:
                x["market_date"] = "2026-09-30"
        self.assertTrue(any(i.path.endswith("market_date") for i in errors(self.v(r))))

    def test_monday_report_may_cite_friday_close(self):
        r = good_report(); r["report_date"] = "2026/10/12"
        for sec in ("us_market", "asia_market"):
            for x in r["sections"][sec]:
                x["market_date"] = "2026-10-09"
        for sec in ("fx", "commodity_rate"):
            for x in r["sections"][sec]:
                x["market_date"] = "2026-10-09" if x["quote_kind"] != "daily_yield" else "2026-10-09"
        issues = vr.validate(r, report_dir_date="2026-10-12")
        self.assertEqual([i for i in errors(issues) if "market_date" in i.path], [])

    def test_us_close_must_be_previous_session(self):
        r = good_report(); r["sections"]["us_market"][0]["market_date"] = "2026-10-07"
        self.assertTrue(any("market_date" in i.path for i in errors(self.v(r))))

    def test_credential_in_source_url(self):
        r = good_report(); r["sections"]["fx"][0]["source_url"] = "https://x.test/q?apikey=abc"
        self.assertTrue(any("credential" in i.msg for i in errors(self.v(r))))

    def test_non_object_report_and_rows(self):
        self.assertTrue(errors(vr.validate([])))
        r = good_report(); r["sections"]["fx"] = ["x"]
        self.assertTrue(errors(self.v(r)))
        r = good_report(); r["sections"] = []
        self.assertTrue(errors(self.v(r)))


class Images(unittest.TestCase):
    def test_valid_images(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); make_png(d / "card.png"); make_png(d / "card_preview.png")
            self.assertEqual(vr.validate_images(d / "card.png", d / "card_preview.png"), [])

    def test_missing_corrupt_and_oversize(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertTrue(vr.validate_images(d / "a.png", d / "b.png"))
            (d / "card.png").write_bytes(b"not a png")
            make_png(d / "card_preview.png")
            msgs = " ".join(i.msg for i in vr.validate_images(d / "card.png", d / "card_preview.png"))
            self.assertIn("cannot be decoded", msgs)
            make_png(d / "card.png")
            data = bytearray((d / "card.png").read_bytes()); data[40] ^= 0xFF
            (d / "card.png").write_bytes(bytes(data))
            self.assertIn("CRC", " ".join(i.msg for i in vr.validate_images(d / "card.png", d / "card_preview.png")))
            make_png(d / "card_preview.png", pad=vr.LINE_PREVIEW_MAX + 1)
            make_png(d / "card.png")
            self.assertIn("exceeds", " ".join(i.msg for i in vr.validate_images(d / "card.png", d / "card_preview.png")))


class Cli(unittest.TestCase):
    def test_cli_exit_codes_and_dir_date(self):
        with tempfile.TemporaryDirectory() as d:
            day = Path(d) / "2026-10-07"; day.mkdir()
            (day / "report.json").write_text(json.dumps(good_report()), encoding="utf-8")
            self.assertEqual(vr.main([str(day / "report.json"), "--today", "2026-10-07", "--for-push"]), 0)
            self.assertEqual(vr.main([str(day / "report.json"), "--today", "2026-10-09", "--for-push"]), 1)
            self.assertEqual(vr.main([str(day / "nope.json")]), 1)


if __name__ == "__main__":
    unittest.main()
