from __future__ import annotations

from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from nautilus_trader.model.data import BarType
from nautilus_trader.persistence.wranglers import BarDataWrangler

from trader_ai.config import InstrumentSpec
from trader_ai.config import NautilusSettings


def generate_synthetic_bars(
    *,
    settings: NautilusSettings,
    instrument,
    bar_type: BarType,
):
    rng = np.random.default_rng(settings.random_seed)
    n = settings.bars_count
    price = 1.10 + np.cumsum(rng.normal(0, 0.0002, n))
    spread = np.abs(rng.normal(0, 0.0003, n))

    bars_df = pd.DataFrame(
        {
            "open": price,
            "high": price + spread,
            "low": price - spread,
            "close": price + rng.normal(0, 0.00005, n),
            "volume": rng.integers(1, 20, n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC"),
    )
    bars_df["high"] = bars_df[["open", "high", "close"]].max(axis=1)
    bars_df["low"] = bars_df[["open", "low", "close"]].min(axis=1)

    return BarDataWrangler(bar_type, instrument).process(bars_df)


def load_csv_bars(*, csv_path: str | Path, instrument, bar_type: BarType):
    raw_path = Path(csv_path)
    bars_df = pd.read_csv(raw_path)
    bars_df["timestamp"] = pd.to_datetime(bars_df["timestamp"], utc=True)
    bars_df = bars_df.set_index("timestamp").sort_index()
    return BarDataWrangler(bar_type, instrument).process(bars_df)


def build_ccxt_cache_path(
    *,
    cache_dir: str | Path,
    instrument_name: str,
    instrument_spec: InstrumentSpec,
    limit: int,
) -> Path:
    raw_cache_dir = Path(cache_dir)
    timeframe = instrument_spec.ccxt_timeframe or "unknown"
    exchange = instrument_spec.ccxt_exchange or "unknown"
    filename = f"{instrument_name}_{exchange}_{timeframe}_{limit}.csv"
    return raw_cache_dir / filename


def build_ccxt_batch_sizes(*, limit: int, batch_size: int = 1000) -> list[int]:
    remaining = int(limit)
    batches: list[int] = []
    while remaining > 0:
        current = min(batch_size, remaining)
        batches.append(current)
        remaining -= current
    return batches


def load_ccxt_bars(
    *,
    cache_dir: str | Path,
    instrument_name: str,
    instrument_spec: InstrumentSpec,
    instrument,
    bar_type: BarType,
    limit: int,
):
    cache_path = build_ccxt_cache_path(
        cache_dir=cache_dir,
        instrument_name=instrument_name,
        instrument_spec=instrument_spec,
        limit=limit,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        return load_csv_bars(csv_path=cache_path, instrument=instrument, bar_type=bar_type)

    if instrument_spec.ccxt_exchange is None or instrument_spec.ccxt_symbol is None:
        raise ValueError(f"Instrument {instrument_name} is missing CCXT metadata")

    exchange_cls = getattr(ccxt, instrument_spec.ccxt_exchange)
    exchange = exchange_cls({"enableRateLimit": True})
    try:
        timeframe = instrument_spec.ccxt_timeframe or "15m"
        timeframe_ms = int(exchange.parse_timeframe(timeframe) * 1000)
        rows = []
        batches = build_ccxt_batch_sizes(limit=limit, batch_size=1000)
        since = exchange.milliseconds() - (limit * timeframe_ms)
        for batch_limit in batches:
            batch_rows = exchange.fetch_ohlcv(
                instrument_spec.ccxt_symbol,
                timeframe=timeframe,
                since=since,
                limit=batch_limit,
            )
            if not batch_rows:
                break
            rows.extend(batch_rows)
            since = batch_rows[-1][0] + timeframe_ms
    finally:
        close = getattr(exchange, "close", None)
        if callable(close):
            close()

    bars_df = pd.DataFrame(
        rows[-limit:],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    ).drop_duplicates(subset=["timestamp"], keep="last")
    bars_df["timestamp"] = pd.to_datetime(bars_df["timestamp"], unit="ms", utc=True)
    bars_df.to_csv(cache_path, index=False)
    return load_csv_bars(csv_path=cache_path, instrument=instrument, bar_type=bar_type)
