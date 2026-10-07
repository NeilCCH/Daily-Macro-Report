import json
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

import helpers  # noqa: F401
import fetch_market_data as fm

UTC = timezone.utc
NOW = datetime(2026, 10, 6, 23, 0, tzinfo=UTC)
KEYS = {"ALPHA_VANTAGE_API_KEY": "SECRET-AV", "TWELVE_DATA_API_KEY": "SECRET-TD", "OIL_PRICE_API_KEY": "SECRET-OIL"}


def td(close="31.2", change="0.1", pct="0.3", ts="2026-10-06T22:30:00Z"):
    return {"close": close, "change": change, "percent_change": pct, "last_quote_at": ts}


def oil(price=80, changes=None, created="2026-10-06T22:00:00Z"):
    d = {"price": price, "created_at": created}
    if changes is not False:
        d["changes"] = changes if changes is not None else {"24h": {"amount": 1.0, "percent": 1.2}}
    return {"status": "success", "data": d}


def av(rows):
    return {"data": [{"date": d, "value": v} for d, v in rows]}


def router(td_resp=None, oil_resp=None, av_resp=None):
    def fake(url, params=None, headers=None):
        for resp, marker in ((td_resp, "twelvedata"), (oil_resp, "oilpriceapi"), (av_resp, "alphavantage")):
            if marker in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp() if callable(resp) else resp
        raise AssertionError(url)
    return fake


DEFAULT = object()


def run(td_resp=DEFAULT, oil_resp=DEFAULT, av_resp=DEFAULT, now=NOW):
    td_resp = td() if td_resp is DEFAULT else td_resp
    oil_resp = oil() if oil_resp is DEFAULT else oil_resp
    av_resp = av([("2026-10-06", "5.31"), ("2026-10-05", "5.28")]) if av_resp is DEFAULT else av_resp
    with mock.patch.dict(os.environ, KEYS), mock.patch.object(fm, "_http_get", router(td_resp, oil_resp, av_resp)):
        return fm.collect(now)


class Fetch(unittest.TestCase):
    def labels(self, result):
        return {r["label"] for rows in result.values() for r in rows}

    def test_all_good_has_provenance(self):
        result, diags = run()
        self.assertEqual(len(result["fx"]), 4)
        self.assertEqual(len(result["commodity_rate"]), 4)
        self.assertEqual(diags, [])
        r = result["fx"][0]
        for k in ("source", "source_url", "as_of", "market_date", "fetched_at", "quote_kind"):
            self.assertTrue(r[k], k)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_stale_quote_is_withheld_with_reason(self):
        result, diags = run(td_resp=td(ts="2020-01-01"))
        self.assertNotIn("fx", result)
        self.assertEqual({d["status"] for d in diags if d["item"].startswith("USD")}, {"stale"})

    def test_value_without_timestamp_is_withheld(self):
        result, diags = run(td_resp={"close": "31.2", "change": "0.1", "percent_change": "0.3"})
        self.assertNotIn("fx", result)
        self.assertTrue(all(d["status"] == "stale" for d in diags if d["item"].startswith("USD")))

    def test_null_close_does_not_abort_other_items(self):
        result, diags = run(td_resp=td(close=None))
        self.assertNotIn("fx", result)
        self.assertIn("WTI 原油", self.labels(result))
        self.assertIn("美 10Y 公債", self.labels(result))
        self.assertIn("schema_error", {d["status"] for d in diags})

    def test_null_changes_object_is_unknown_not_flat(self):
        result, diags = run(oil_resp=oil(changes={"24h": None}))
        wti = next(r for r in result["commodity_rate"] if r["label"] == "WTI 原油")
        self.assertEqual(wti["dir"], "unknown")
        self.assertEqual(wti["change_pts"], "")
        self.assertIn("unknown_change", {d["status"] for d in diags})

    def test_changes_null_and_missing(self):
        r1, _ = run(oil_resp={"status": "success", "data": {"price": 80, "created_at": "2026-10-06T22:00:00Z", "changes": None}})
        r2, _ = run(oil_resp={"status": "success", "data": {"price": 80, "created_at": "2026-10-06T22:00:00Z"}})
        for result in (r1, r2):
            self.assertEqual(next(r for r in result["commodity_rate"] if r["label"] == "WTI 原油")["dir"], "unknown")

    def test_missing_change_is_not_flat_but_explicit_zero_is(self):
        result, _ = run(td_resp=td(change=None, pct=None))
        self.assertEqual(result["fx"][0]["dir"], "unknown")
        result, _ = run(td_resp=td(change="0", pct="0"))
        self.assertEqual(result["fx"][0]["dir"], "flat")

    def test_nan_inf_bool_and_string_numbers(self):
        for bad in ("NaN", "nan", "Infinity", "-inf", True, [], {}, ""):
            self.assertFalse(fm._is_number(bad), bad)
        self.assertTrue(fm._is_number("1,234.5"))
        self.assertTrue(fm._is_number(3))
        result, diags = run(td_resp=td(close="NaN"))
        self.assertNotIn("fx", result)

    def test_oil_price_string(self):
        result, _ = run(oil_resp=oil(price="80.5"))
        self.assertEqual(next(r for r in result["commodity_rate"] if r["label"] == "WTI 原油")["value"], "$80.50")

    def test_oil_price_bool_rejected(self):
        result, diags = run(oil_resp=oil(price=True))
        self.assertNotIn("WTI 原油", self.labels(result))

    def test_http_failures_are_classified(self):
        for exc_status in ("rate_limited", "auth_error", "http_error", "timeout", "network_error", "schema_error"):
            with self.subTest(exc_status):
                result, diags = run(td_resp=fm.DataIssue(exc_status))
                self.assertNotIn("fx", result)
                self.assertIn(exc_status, {d["status"] for d in diags})

    def test_non_dict_and_list_bodies(self):
        for body in ([], None, "text", 5):
            with self.subTest(body=body):
                result, diags = run(td_resp=body)
                self.assertNotIn("fx", result)
                self.assertIn("schema_error", {d["status"] for d in diags})

    def test_twelvedata_error_body_is_not_data(self):
        result, diags = run(td_resp={"status": "error", "code": 429, "message": "limit"})
        self.assertNotIn("fx", result)
        self.assertIn("rate_limited", {d["status"] for d in diags})

    def test_alpha_vantage_rate_limit_notice(self):
        result, diags = run(av_resp={"Information": "Rate limit"})
        self.assertNotIn("美 10Y 公債", self.labels(result))
        self.assertIn("rate_limited", {d["status"] for d in diags})

    def test_empty_yield_arrays(self):
        for body in ({"data": []}, {"data": None}, {"data": [{"date": "2026-10-06", "value": "."}]}, {}):
            with self.subTest(body=body):
                result, diags = run(av_resp=body)
                self.assertNotIn("美 10Y 公債", self.labels(result))
                self.assertIn("schema_error", {d["status"] for d in diags})

    def test_yield_publication_delay_is_accepted_but_old_is_stale(self):
        result, _ = run(av_resp=av([("2026-10-05", "5.28"), ("2026-10-02", "5.20")]))
        self.assertIn("美 10Y 公債", self.labels(result))
        result, diags = run(av_resp=av([("2026-09-01", "5.0"), ("2026-08-31", "5.0")]))
        self.assertNotIn("美 10Y 公債", self.labels(result))
        self.assertIn("stale", {d["status"] for d in diags})

    def test_single_yield_observation_direction_unknown(self):
        result, _ = run(av_resp=av([("2026-10-06", "5.31")]))
        y = next(r for r in result["commodity_rate"] if r["label"] == "美 10Y 公債")
        self.assertEqual(y["dir"], "unknown")

    def test_one_of_eight_fails_rest_still_output_valid_json(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 2:
                raise TypeError("boom")
            return td()
        result, diags = run(td_resp=flaky)
        self.assertEqual(len(result["fx"]), 3)  # the 2nd of the 4 fx calls raised; the other three survive
        json.dumps(result)
        self.assertIn("unexpected_error", {d["status"] for d in diags})

    def test_zero_valid_rows_gives_diagnostics(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result, diags = fm.collect(NOW)
        self.assertEqual(result, {})
        self.assertEqual({d["status"] for d in diags}, {"missing_key"})
        self.assertEqual(len(diags), 8)

    def test_missing_requests_is_reported_not_crashed(self):
        with mock.patch.dict(os.environ, KEYS), mock.patch.object(fm, "requests", None):
            result, diags = fm.collect(NOW)
        self.assertEqual(result, {})
        self.assertEqual({d["status"] for d in diags}, {"missing_dependency"})

    @unittest.skipIf(fm.requests is None, "requests not installed for this interpreter")
    def test_requests_exception_text_never_leaks_key(self):
        import requests
        exc = requests.ConnectionError("HTTPSConnectionPool url=...apikey=SECRET-TD")
        with mock.patch.dict(os.environ, KEYS), mock.patch.object(fm.requests, "get", side_effect=exc):
            result, diags = fm.collect(NOW)
        self.assertNotIn("SECRET", json.dumps(diags))

    @unittest.skipIf(fm.requests is None, "requests not installed for this interpreter")
    def test_http_status_mapping(self):
        import requests

        def resp(code, body=None, raises=False):
            r = mock.Mock(status_code=code)
            r.json.side_effect = ValueError("no json") if raises else None
            r.json.return_value = body
            return r
        cases = [(429, "rate_limited"), (401, "auth_error"), (403, "auth_error"), (500, "http_error"), (404, "http_error")]
        for code, status in cases:
            with mock.patch.object(fm.requests, "get", return_value=resp(code)):
                with self.assertRaises(fm.DataIssue) as cm:
                    fm._http_get("https://x.test")
                self.assertEqual(cm.exception.status, status, code)
        with mock.patch.object(fm.requests, "get", return_value=resp(200, raises=True)):
            with self.assertRaises(fm.DataIssue) as cm:
                fm._http_get("https://x.test")
            self.assertEqual(cm.exception.status, "schema_error")

    def test_source_url_has_no_credentials(self):
        result, _ = run()
        for rows in result.values():
            for r in rows:
                self.assertNotRegex(r["source_url"], r"(?i)apikey|token")


if __name__ == "__main__":
    unittest.main()
