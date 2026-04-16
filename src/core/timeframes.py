from __future__ import annotations


_CANONICAL_ALIASES = {
    "1min": "1Min",
    "1m": "1Min",
    "5min": "5Min",
    "5m": "5Min",
    "15min": "15Min",
    "15m": "15Min",
    "30min": "30Min",
    "30m": "30Min",
    "1hour": "1Hour",
    "1h": "1Hour",
    "4hour": "4Hour",
    "4h": "4Hour",
    "1day": "1Day",
    "1d": "1Day",
}

_TIMEFRAME_TO_CCXT = {
    "1Min": "1m",
    "5Min": "5m",
    "15Min": "15m",
    "30Min": "30m",
    "1Hour": "1h",
    "4Hour": "4h",
    "1Day": "1d",
}

_TIMEFRAME_TO_MINUTES = {
    "1Min": 1,
    "5Min": 5,
    "15Min": 15,
    "30Min": 30,
    "1Hour": 60,
    "4Hour": 240,
    "1Day": 1440,
}


def canonical_timeframe(timeframe: str | None, default: str = "5Min") -> str:
    raw = str(timeframe or default).strip()
    key = raw.replace(" ", "").lower()
    return _CANONICAL_ALIASES.get(key, raw)


def timeframe_to_ccxt(timeframe: str | None, default: str = "5Min") -> str:
    canonical = canonical_timeframe(timeframe, default=default)
    return _TIMEFRAME_TO_CCXT.get(canonical, canonical)


def timeframe_to_minutes(timeframe: str | None, default: str = "5Min") -> int:
    canonical = canonical_timeframe(timeframe, default=default)
    return _TIMEFRAME_TO_MINUTES.get(canonical, 5)
