#!/usr/bin/env python3
"""Fetch what this report can get from free market-data APIs.

Covers: 10Y Treasury yield (Alpha Vantage), 4 FX pairs + gold (Twelve Data),
WTI/Brent (Oil Price API).

Does NOT cover, and never will on these free tiers (verified 2026-07-14):
S&P 500, NASDAQ, 費半 SOX, 日經 225, 台股加權, 台指期夜盤 (indices aren't on
Twelve Data's free plan) or 白銀/silver (XAG/USD needs a paid Twelve Data
plan; Oil Price API is oil-only). Those six still need WebSearch.

Env vars (any missing -> that data point is skipped with a diagnostic):
  ALPHA_VANTAGE_API_KEY
  TWELVE_DATA_API_KEY
  OIL_PRICE_API_KEY

Usage: fetch_market_data.py [--diagnostics-file PATH]

stdout: a JSON object with "fx" and/or "commodity_rate" row lists, only for
data points that were fetched AND judged fresh. Each row carries the display
fields (label/value/change_pts/change_pct/dir) plus provenance:
  source, source_url (never contains an API key), as_of, market_date,
  fetched_at, quote_kind.

`dir` is "up"/"down"/"flat" only when the change is known; "flat" means a
confirmed zero change. A missing comparison value gives "unknown" (it is NOT
treated as flat).

Everything that was NOT usable is explained in the diagnostics list (stderr
summary + optional --diagnostics-file JSON) so the caller knows exactly what
to follow up with WebSearch: missing_key, timeout, network_error,
rate_limited, auth_error, http_error, api_error, schema_error, stale,
missing_dependency, unexpected_error. Diagnostics never contain credentials.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timezone

import quote_freshness as qf

try:  # keep the script alive (with diagnostics) if the interpreter lacks requests
    import requests
except ImportError:  # pragma: no cover - exercised via mock in tests
    requests = None

TIMEOUT = 15
UTC = timezone.utc


class DataIssue(Exception):
    """A data point could not be used. `status` is a stable machine-readable code."""

    def __init__(self, status: str, detail: str = ""):
        super().__init__(f"{status}: {detail}" if detail else status)
        self.status = status
        self.detail = detail


# --------------------------------------------------------------------------
# value helpers
# --------------------------------------------------------------------------

def to_float(v) -> float | None:
    """Finite float from a number or numeric string; None for bool/NaN/Inf/junk."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) else None


def _is_number(v) -> bool:
    return to_float(v) is not None


def _dir_from_change(change) -> str | None:
    c = to_float(change)
    if c is None:
        return None
    if c > 0:
        return "up"
    if c < 0:
        return "down"
    return "flat"


def _obj(v, what: str) -> dict:
    if not isinstance(v, dict):
        raise DataIssue("schema_error", f"{what} is not an object")
    return v


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _http_get(url: str, params: dict | None = None, headers: dict | None = None):
    """GET and return the parsed JSON body, mapping every failure to DataIssue.

    Exception text from `requests` can embed the full URL (including the API
    key in the query string), so only the exception class name is ever kept.
    """
    if requests is None:
        raise DataIssue("missing_dependency", "python 'requests' is not installed for this interpreter")
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    except requests.Timeout:
        raise DataIssue("timeout") from None
    except requests.RequestException as exc:
        raise DataIssue("network_error", type(exc).__name__) from None
    code = resp.status_code
    if code == 429:
        raise DataIssue("rate_limited", "HTTP 429")
    if code in (401, 403):
        raise DataIssue("auth_error", f"HTTP {code}")
    if code >= 400:
        raise DataIssue("http_error", f"HTTP {code}")
    try:
        return resp.json()
    except ValueError:
        raise DataIssue("schema_error", "response is not JSON") from None


def _api_error_status(code) -> str:
    c = to_float(code)
    if c == 429:
        return "rate_limited"
    if c in (401, 403):
        return "auth_error"
    return "api_error"


def _fetched_at(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# fetchers (each returns a row dict or raises DataIssue)
# --------------------------------------------------------------------------

def fetch_treasury_yield(now: datetime) -> dict:
    key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not key:
        raise DataIssue("missing_key", "ALPHA_VANTAGE_API_KEY")
    body = _obj(_http_get(
        "https://www.alphavantage.co/query",
        params={"function": "TREASURY_YIELD", "interval": "daily", "maturity": "10year", "apikey": key},
    ), "response")
    for note_key in ("Note", "Information"):
        if note_key in body:
            raise DataIssue("rate_limited", "Alpha Vantage returned a usage notice")
    if "Error Message" in body:
        raise DataIssue("api_error", "Alpha Vantage returned an error message")
    data = body.get("data")
    if not isinstance(data, list):
        raise DataIssue("schema_error", "'data' is not a list")
    series = []
    for d in data:
        if not isinstance(d, dict):
            continue
        val = to_float(d.get("value"))
        obs = qf.parse_as_of(d.get("date"))[1]
        if val is not None and obs is not None:
            series.append((obs, val))
    if not series:
        raise DataIssue("schema_error", "no usable yield observations")
    series.sort(key=lambda t: t[0], reverse=True)
    obs_date, latest = series[0]
    ok, reason = qf.check_daily_yield(obs_date, now)
    if not ok:
        raise DataIssue("stale", reason)
    dirn, change_pts = "unknown", ""
    if len(series) > 1:
        diff = latest - series[1][1]
        dirn = "up" if diff > 0 else "down" if diff < 0 else "flat"
        change_pts = f"{abs(diff):.2f}%"
    return {
        "label": "美 10Y 公債", "value": f"{latest:.2f}%", "change_pts": change_pts, "change_pct": "", "dir": dirn,
        "source": "Alpha Vantage", "source_url": "https://www.alphavantage.co/query?function=TREASURY_YIELD&maturity=10year",
        "as_of": obs_date.isoformat(), "market_date": obs_date.isoformat(), "fetched_at": _fetched_at(now),
        "quote_kind": "daily_yield",
    }


def fetch_twelve_data_quote(symbol: str, label: str, fmt, now: datetime, pts_fmt=None) -> dict:
    key = os.environ.get("TWELVE_DATA_API_KEY")
    if not key:
        raise DataIssue("missing_key", "TWELVE_DATA_API_KEY")
    data = _obj(_http_get("https://api.twelvedata.com/quote", params={"symbol": symbol, "apikey": key}), "response")
    if data.get("status") == "error":
        raise DataIssue(_api_error_status(data.get("code")), f"Twelve Data error code {data.get('code')}")
    close = to_float(data.get("close"))
    if close is None:
        raise DataIssue("schema_error", "'close' is missing or not a finite number")
    as_of_dt = as_of_date = None
    for field in ("last_quote_at", "timestamp", "datetime"):
        as_of_dt, as_of_date = qf.parse_as_of(data.get(field))
        if as_of_dt or as_of_date:
            break
    ok, reason = qf.check_realtime(as_of_dt, as_of_date, now)
    if not ok:
        raise DataIssue("stale", reason)
    change = to_float(data.get("change"))
    pct = to_float(data.get("percent_change"))
    dirn = _dir_from_change(change) or ("up" if (pct or 0) > 0 else "down" if (pct or 0) < 0 else "unknown")
    if change is None and pct == 0:
        dirn = "flat"
    pf = pts_fmt or fmt
    as_of = as_of_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if as_of_dt else as_of_date.isoformat()
    market_date = (as_of_dt.date() if as_of_dt else as_of_date).isoformat()
    return {
        "label": label, "value": fmt(close),
        "change_pts": pf(abs(change)) if change is not None else "",
        "change_pct": f"{abs(pct):.2f}%" if pct is not None else "",
        "dir": dirn,
        "source": "Twelve Data", "source_url": f"https://api.twelvedata.com/quote?symbol={symbol}",
        "as_of": as_of, "market_date": market_date, "fetched_at": _fetched_at(now), "quote_kind": "realtime",
    }


def fetch_oil_price(code: str, label: str, now: datetime) -> dict:
    key = os.environ.get("OIL_PRICE_API_KEY")
    if not key:
        raise DataIssue("missing_key", "OIL_PRICE_API_KEY")
    body = _obj(_http_get(
        "https://api.oilpriceapi.com/v1/prices/latest", params={"code": code},
        headers={"Authorization": f"Token {key}"},
    ), "response")
    if body.get("status") == "error":
        raise DataIssue("api_error", "Oil Price API returned an error status")
    d = _obj(body.get("data"), "'data'")
    price = to_float(d.get("price"))
    if price is None:
        raise DataIssue("schema_error", "'price' is missing or not a finite number")
    as_of_dt, as_of_date = qf.parse_as_of(d.get("created_at"))
    ok, reason = qf.check_realtime(as_of_dt, as_of_date, now)
    if not ok:
        raise DataIssue("stale", reason)
    changes = d.get("changes")
    ch = changes.get("24h") if isinstance(changes, dict) else None
    ch = ch if isinstance(ch, dict) else {}
    amount, pct = to_float(ch.get("amount")), to_float(ch.get("percent"))
    dirn = _dir_from_change(amount) or "unknown"
    as_of = as_of_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if as_of_dt else as_of_date.isoformat()
    market_date = (as_of_dt.date() if as_of_dt else as_of_date).isoformat()
    return {
        "label": label, "value": f"${price:,.2f}",
        "change_pts": f"${abs(amount):,.2f}" if amount is not None else "",
        "change_pct": f"{abs(pct):.1f}%" if pct is not None else "",
        "dir": dirn,
        "source": "Oil Price API", "source_url": f"https://api.oilpriceapi.com/v1/prices/latest?by_code={code}",
        "as_of": as_of, "market_date": market_date, "fetched_at": _fetched_at(now), "quote_kind": "realtime",
    }


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def collect(now: datetime | None = None) -> tuple[dict, list[dict]]:
    """Run every fetcher independently. Returns (result, diagnostics)."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    fx_specs = [
        ("USD/TWD", "USD / TWD", lambda v: f"{v:.2f}"),
        ("USD/JPY", "USD / JPY", lambda v: f"{v:.1f}"),
        ("USD/CNY", "USD / CNY", lambda v: f"{v:.2f}"),
        ("USD/EUR", "USD / EUR", lambda v: f"{v:.3f}"),
    ]
    jobs = [("fx", label, lambda s=sym, l=label, f=fmt: fetch_twelve_data_quote(s, l, f, now))
            for sym, label, fmt in fx_specs]
    jobs += [("commodity_rate", label, lambda c=code, l=label: fetch_oil_price(c, l, now))
             for code, label in (("WTI_USD", "WTI 原油"), ("BRENT_CRUDE_USD", "Brent 原油"))]
    jobs.append(("commodity_rate", "黃金", lambda: fetch_twelve_data_quote(
        "XAU/USD", "黃金", lambda v: f"${v:,.1f}", now, pts_fmt=lambda v: f"${v:,.1f}")))
    jobs.append(("commodity_rate", "美 10Y 公債", lambda: fetch_treasury_yield(now)))

    result: dict[str, list] = {}
    diagnostics: list[dict] = []
    for section, label, job in jobs:
        try:
            row = job()
        except DataIssue as issue:
            diagnostics.append({"item": label, "status": issue.status, "detail": issue.detail})
            continue
        except Exception as exc:  # noqa: BLE001 - last-resort boundary, always recorded
            diagnostics.append({"item": label, "status": "unexpected_error", "detail": type(exc).__name__})
            continue
        result.setdefault(section, []).append(row)
        if row["dir"] == "unknown":
            diagnostics.append({"item": label, "status": "unknown_change",
                                "detail": "no comparison value; direction left unknown (not flat)"})
    return result, diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostics-file", default=None)
    args = parser.parse_args(argv)
    result, diagnostics = collect()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    for d in diagnostics:
        print(f"[fetch] {d['item']}: {d['status']}" + (f" ({d['detail']})" if d["detail"] else ""), file=sys.stderr)
    if not result:
        print("[fetch] no usable rows; every item needs WebSearch follow-up", file=sys.stderr)
    if args.diagnostics_file:
        with open(args.diagnostics_file, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": _fetched_at(datetime.now(UTC)), "diagnostics": diagnostics}, f,
                      ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
