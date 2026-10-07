"""Freshness rules for market quotes (pure functions, no network).

Different data kinds age differently, so there is no single "N hours" rule:

  realtime   FX / oil / gold quotes that trade ~24h on weekdays. Fresh when
             the quote is at most 12h older than the last moment the market
             was open (so a Friday-close quote is fine on a weekend or
             Monday morning in Taipei).
  daily_close  an end-of-day close; the session date must be one of the
             caller-supplied expected dates.
  daily_yield  treasury yield series that are published with a delay; the
             observation may lag the last US business day by up to 2
             business days (holidays, publication delay).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc
REALTIME_MAX_AGE = timedelta(hours=12)
YIELD_MAX_LAG_BUSINESS_DAYS = 2


def prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def business_days_between(older: date, newer: date) -> int:
    """Weekdays strictly after `older` up to and including `newer` (0 if not older)."""
    if newer <= older:
        return 0
    n, d = 0, older
    while d < newer:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _last_friday_close(now: datetime) -> datetime:
    wd = now.weekday()  # Mon=0 .. Sun=6
    fri = (now - timedelta(days=(wd - 4) % 7)).replace(hour=22, minute=0, second=0, microsecond=0)
    return fri if fri <= now else fri - timedelta(days=7)


def _reference_instant(now: datetime) -> datetime:
    """Latest instant a 24/5 quote can be expected to be near.

    Weekend (Fri 22:00 UTC .. Sun 22:00 UTC) and the first 3h after the
    Sunday reopen both fall back to the Friday close.
    """
    wd, hr = now.weekday(), now.hour
    closed = wd == 5 or (wd == 4 and hr >= 22) or (wd == 6 and hr < 22)
    reopen_window = wd == 6 and 22 <= hr < 25  # 22:00-24:00 UTC Sunday
    if closed or reopen_window:
        return _last_friday_close(now)
    if wd == 0 and hr < 1:  # first hour of Monday UTC still belongs to the reopen window
        return _last_friday_close(now)
    return now


def parse_as_of(value) -> tuple[datetime | None, date | None]:
    """Return (aware datetime, None) for epoch/ISO timestamps, (None, date) for date-only."""
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")) or value <= 0:
            return None, None
        try:
            return datetime.fromtimestamp(float(value), UTC), None
        except (OverflowError, OSError, ValueError):
            return None, None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None, None
        if s.isdigit():
            return parse_as_of(int(s))
        try:
            if len(s) == 10:
                return None, date.fromisoformat(s)
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None, None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC), None
    return None, None


def check_realtime(as_of_dt: datetime | None, as_of_date: date | None, now: datetime) -> tuple[bool, str]:
    now = now.astimezone(UTC)
    ref = _reference_instant(now)
    if as_of_dt is not None:
        if as_of_dt > now + timedelta(minutes=5):
            return False, f"as_of {as_of_dt.isoformat()} is in the future"
        if ref - as_of_dt > REALTIME_MAX_AGE:
            return False, f"quote {as_of_dt.isoformat()} is older than {REALTIME_MAX_AGE} before {ref.isoformat()}"
        return True, "ok"
    if as_of_date is not None:
        floor = (ref - timedelta(hours=36)).date()
        if as_of_date < floor:
            return False, f"quote date {as_of_date.isoformat()} is before {floor.isoformat()}"
        if as_of_date > now.date() + timedelta(days=1):
            return False, f"quote date {as_of_date.isoformat()} is in the future"
        return True, "ok"
    return False, "quote has no usable timestamp"


def check_daily_close(market_date: date | None, expected: set[date]) -> tuple[bool, str]:
    if market_date is None:
        return False, "close has no session date"
    if market_date not in expected:
        exp = ", ".join(sorted(d.isoformat() for d in expected))
        return False, f"session date {market_date.isoformat()} not in expected {{{exp}}}"
    return True, "ok"


def check_daily_yield(obs_date: date | None, now: datetime) -> tuple[bool, str]:
    if obs_date is None:
        return False, "yield observation has no date"
    today_us = now.astimezone(UTC).date()
    last_bd = prev_weekday(today_us)
    if obs_date > today_us:
        return False, f"observation {obs_date.isoformat()} is in the future"
    lag = business_days_between(obs_date, last_bd)
    if lag > YIELD_MAX_LAG_BUSINESS_DAYS:
        return False, f"observation {obs_date.isoformat()} lags the last business day {last_bd.isoformat()} by {lag} business days"
    return True, "ok"
