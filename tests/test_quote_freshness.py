import unittest
from datetime import date, datetime, timezone

import helpers  # noqa: F401
import quote_freshness as qf

UTC = timezone.utc


class Freshness(unittest.TestCase):
    def test_fresh_realtime_weekday(self):
        now = datetime(2026, 10, 6, 23, 0, tzinfo=UTC)  # Wed 07:00 Taipei
        self.assertTrue(qf.check_realtime(*qf.parse_as_of("2026-10-06T22:30:00Z"), now)[0])

    def test_stale_2020_date_rejected(self):
        now = datetime(2026, 10, 6, 23, 0, tzinfo=UTC)
        ok, reason = qf.check_realtime(*qf.parse_as_of("2020-01-01"), now)
        self.assertFalse(ok)
        self.assertIn("2020-01-01", reason)

    def test_missing_timestamp_rejected(self):
        ok, reason = qf.check_realtime(None, None, datetime(2026, 10, 6, tzinfo=UTC))
        self.assertFalse(ok)
        self.assertIn("no usable timestamp", reason)

    def test_monday_morning_taipei_accepts_friday_close(self):
        mon_taipei_0700 = datetime(2026, 10, 11, 23, 0, tzinfo=UTC)  # Sunday 23:00 UTC
        self.assertTrue(qf.check_realtime(*qf.parse_as_of("2026-10-09T21:55:00Z"), mon_taipei_0700)[0])
        self.assertFalse(qf.check_realtime(*qf.parse_as_of("2026-10-08T21:55:00Z"), mon_taipei_0700)[0])

    def test_weekend_accepts_friday_close(self):
        sat = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
        self.assertTrue(qf.check_realtime(*qf.parse_as_of("2026-10-09T21:59:00Z"), sat)[0])

    def test_future_timestamp_rejected(self):
        now = datetime(2026, 10, 6, 23, 0, tzinfo=UTC)
        self.assertFalse(qf.check_realtime(*qf.parse_as_of("2026-10-07T23:00:00Z"), now)[0])

    def test_epoch_and_iso_parse_to_same_instant(self):
        a, _ = qf.parse_as_of(1791328200)
        b, _ = qf.parse_as_of(a.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(a, b)

    def test_bad_inputs_parse_to_none(self):
        for bad in (None, True, "", "garbage", float("nan"), -5, {}):
            self.assertEqual(qf.parse_as_of(bad), (None, None), bad)

    def test_daily_close_expected_dates(self):
        exp = {date(2026, 10, 6)}
        self.assertTrue(qf.check_daily_close(date(2026, 10, 6), exp)[0])
        self.assertFalse(qf.check_daily_close(date(2026, 10, 2), exp)[0])  # last week's Friday on a Wednesday
        self.assertFalse(qf.check_daily_close(None, exp)[0])

    def test_monday_report_cites_friday_close(self):
        monday = date(2026, 10, 12)
        self.assertEqual(qf.prev_weekday(monday), date(2026, 10, 9))

    def test_yield_publication_lag_is_tolerated(self):
        now = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)  # last business day = Tue 10/6
        self.assertTrue(qf.check_daily_yield(date(2026, 10, 6), now)[0])
        self.assertTrue(qf.check_daily_yield(date(2026, 10, 5), now)[0])  # one day behind: normal
        self.assertTrue(qf.check_daily_yield(date(2026, 10, 2), now)[0])  # 2 business days (holiday/delay)
        self.assertFalse(qf.check_daily_yield(date(2026, 9, 28), now)[0])
        self.assertFalse(qf.check_daily_yield(None, now)[0])


if __name__ == "__main__":
    unittest.main()
