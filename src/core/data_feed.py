"""
Data fetching, pagination, and indicator enrichment.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Sequence

import pandas as pd
from loguru import logger

from src.core.base_broker import BaseBroker
from src.core.indicator_engine import IndicatorPipeline
from src.intelligence.regime_detector import RegimeDetector

_TIMEFRAME_MAP = {
    "1Min": "1m",
    "5Min": "5m",
    "15Min": "15m",
    "30Min": "30m",
    "1Hour": "1h",
    "1Day": "1d",
}

_TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "1d": 1440,
}


class DataFeed:
    """Fetch and enrich market data for runtime and research workflows."""

    def __init__(self, broker: BaseBroker, config: dict):
        self.broker = broker
        self.config = config

        raw_tf = config.get("data", {}).get("candle_interval", "5Min")
        self.timeframe = _TIMEFRAME_MAP.get(raw_tf, raw_tf)
        self.lookback_days = int(config.get("data", {}).get("lookback_days", 60))
        self.max_bars_per_request = int(
            config.get("data", {}).get("max_bars_per_request", 1000)
        )

        self._cache: dict[str, pd.DataFrame] = {}
        self._last_update: dict[str, datetime] = {}
        self.regime_detector = RegimeDetector(config=config)
        self.indicator_pipeline = IndicatorPipeline(self.regime_detector)

    def get_data(
        self,
        symbol: str,
        force_refresh: bool = False,
        strategies: Sequence | None = None,
    ) -> pd.DataFrame:
        """
        Fetch the default lookback window with paginated historical requests.
        """
        if not force_refresh and symbol in self._cache:
            cached = self.indicator_pipeline.enrich(self._cache[symbol], strategies)
            self._cache[symbol] = cached
            return cached

        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=self.lookback_days)
        return self.get_historical_range(
            symbol,
            start_date,
            end_date,
            strategies=strategies,
            cache_symbol=symbol,
        )

    def get_historical_range(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        *,
        strategies: Sequence | None = None,
        cache_symbol: str | None = None,
    ) -> pd.DataFrame:
        """
        Fetch and enrich a specific date range using paginated broker requests.
        """
        logger.info(f"Fetching historical range for {symbol}: {start_date} to {end_date}")

        df = self._fetch_range(symbol, start_date, end_date)
        if df.empty:
            return df

        enriched = self.indicator_pipeline.enrich(df, strategies)
        if cache_symbol:
            self._cache[cache_symbol] = enriched
            self._last_update[cache_symbol] = datetime.utcnow()
        return enriched

    def refresh_all(
        self,
        symbols: list[str],
        strategies: Sequence | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Refresh the active symbol universe."""
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            try:
                result[symbol] = self.get_data(
                    symbol,
                    force_refresh=True,
                    strategies=strategies,
                )
            except Exception as exc:
                logger.error(f"Failed to refresh {symbol}: {exc}")
                result[symbol] = pd.DataFrame()
        return result

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Return the latest cached close or fallback ticker price."""
        if symbol in self._cache and not self._cache[symbol].empty:
            return float(self._cache[symbol]["close"].iloc[-1])
        try:
            ticker = self.broker.fetch_ticker(symbol)
            return float(ticker.get("last", 0.0) or 0.0)
        except Exception:
            return None

    def get_vwap(self, symbol: str) -> Optional[float]:
        """Return the latest cached VWAP value."""
        if symbol in self._cache and not self._cache[symbol].empty:
            df = self._cache[symbol]
            if "vwap" in df.columns:
                return float(df["vwap"].iloc[-1])
        return None

    def clear_cache(self):
        """Clear the local data cache."""
        self._cache.clear()
        self._last_update.clear()
        logger.debug("Data cache cleared")

    @property
    def cached_symbols(self) -> list[str]:
        return list(self._cache.keys())

    def _fetch_range(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        all_frames: list[pd.DataFrame] = []
        since_ms = int(start_date.timestamp() * 1000)
        end_ms = int(end_date.timestamp() * 1000)
        step_ms = self._timeframe_delta_ms

        while since_ms <= end_ms:
            batch = self.broker.fetch_ohlcv(
                symbol,
                timeframe=self.timeframe,
                limit=self.max_bars_per_request,
                since=since_ms,
            )
            if batch.empty:
                break

            all_frames.append(batch)
            last_ts = int(batch.index[-1].timestamp() * 1000)

            if last_ts >= end_ms or last_ts < since_ms:
                break

            since_ms = last_ts + step_ms

        if not all_frames:
            return pd.DataFrame()

        combined = pd.concat(all_frames)
        combined = combined[~combined.index.duplicated(keep="first")]
        combined.sort_index(inplace=True)
        mask = (combined.index >= start_date) & (combined.index <= end_date)
        return combined.loc[mask].copy()

    @property
    def _timeframe_delta_ms(self) -> int:
        minutes = _TIMEFRAME_MINUTES.get(self.timeframe, 5)
        return minutes * 60 * 1000
