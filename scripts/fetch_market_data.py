#!/usr/bin/env python3
"""Fetch what this report can get from free market-data APIs.

Covers: 10Y Treasury yield (Alpha Vantage), 4 FX pairs + gold (Twelve Data),
WTI/Brent (Oil Price API).

Does NOT cover, and never will on these free tiers (verified 2026-07-14):
S&P 500, NASDAQ, 費半 SOX, 日經 225, 台股加權, 台指期夜盤 (indices aren't on
Twelve Data's free plan) or 白銀/silver (XAG/USD needs a paid Twelve Data
plan; Oil Price API is oil-only). Those six still need WebSearch.

Env vars required (any missing -> that data point is just skipped, not
fatal for the rest):
  ALPHA_VANTAGE_API_KEY
  TWELVE_DATA_API_KEY
  OIL_PRICE_API_KEY

Usage: fetch_market_data.py
Prints a JSON object with "fx" and/or "commodity_rate" row lists -- only
for the data points that were fetched successfully. A row that fails
(network error, rate limit, paid-only symbol) is omitted entirely, not
written as null or "N/A", so the caller knows exactly what to WebSearch
instead.
"""
import json
import os

import requests

TIMEOUT = 15


def _is_number(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _dir_from_change(change) -> str | None:
    if not _is_number(change):
        return None
    c = float(change)
    if c > 0:
        return "up"
    if c < 0:
        return "down"
    return "flat"


def fetch_treasury_yield() -> dict | None:
    key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not key:
        return None
    try:
        resp = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TREASURY_YIELD",
                "interval": "daily",
                "maturity": "10year",
                "apikey": key,
            },
            timeout=TIMEOUT,
        )
        series = [d for d in resp.json().get("data", []) if _is_number(d.get("value"))]
        if not series:
            return None
        latest = float(series[0]["value"])
        dirn = "flat"
        if len(series) > 1:
            prev = float(series[1]["value"])
            dirn = "up" if latest > prev else "down" if latest < prev else "flat"
        return {"label": "美 10Y 公債", "value": f"{latest:.2f}%", "change_pct": "", "dir": dirn}
    except (requests.RequestException, ValueError, KeyError):
        return None


def fetch_twelve_data_quote(symbol: str, label: str, fmt) -> dict | None:
    key = os.environ.get("TWELVE_DATA_API_KEY")
    if not key:
        return None
    try:
        resp = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": symbol, "apikey": key},
            timeout=TIMEOUT,
        )
        data = resp.json()
        if data.get("status") == "error" or "close" not in data:
            return None
        close = float(data["close"])
        pct = data.get("percent_change")
        dirn = _dir_from_change(data.get("change")) or "flat"
        return {
            "label": label,
            "value": fmt(close),
            "change_pct": f"{abs(float(pct)):.2f}%" if _is_number(pct) else "",
            "dir": dirn,
        }
    except (requests.RequestException, ValueError, KeyError):
        return None


def fetch_oil_price(code: str, label: str) -> dict | None:
    key = os.environ.get("OIL_PRICE_API_KEY")
    if not key:
        return None
    try:
        resp = requests.get(
            "https://api.oilpriceapi.com/v1/prices/latest",
            params={"code": code},
            headers={"Authorization": f"Token {key}"},
            timeout=TIMEOUT,
        )
        d = resp.json().get("data", {})
        price = d.get("price")
        if price is None:
            return None
        ch = d.get("changes", {}).get("24h", {})
        dirn = _dir_from_change(ch.get("amount")) or "flat"
        pct = ch.get("percent")
        return {
            "label": label,
            "value": f"${price:,.2f}",
            "change_pct": f"{abs(float(pct)):.1f}%" if _is_number(pct) else "",
            "dir": dirn,
        }
    except (requests.RequestException, ValueError, KeyError):
        return None


def main() -> None:
    fx_specs = [
        ("USD/TWD", "USD / TWD", lambda v: f"{v:.2f}"),
        ("USD/JPY", "USD / JPY", lambda v: f"{v:.1f}"),
        ("USD/CNY", "USD / CNY", lambda v: f"{v:.2f}"),
        ("USD/EUR", "USD / EUR", lambda v: f"{v:.3f}"),
    ]
    fx_rows = [
        row
        for row in (fetch_twelve_data_quote(sym, label, fmt) for sym, label, fmt in fx_specs)
        if row
    ]

    commodity_rows = []
    for code, label in (("WTI_USD", "WTI 原油"), ("BRENT_CRUDE_USD", "Brent 原油")):
        row = fetch_oil_price(code, label)
        if row:
            commodity_rows.append(row)

    gold_row = fetch_twelve_data_quote("XAU/USD", "黃金", lambda v: f"${v:,.1f}")
    if gold_row:
        commodity_rows.append(gold_row)

    yield_row = fetch_treasury_yield()
    if yield_row:
        commodity_rows.append(yield_row)

    result = {}
    if fx_rows:
        result["fx"] = fx_rows
    if commodity_rows:
        result["commodity_rate"] = commodity_rows

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
