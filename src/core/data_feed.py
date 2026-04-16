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
from src.core.timeframes import canonical_timeframe, timeframe_to_ccxt, timeframe_to_minutes
from src.intelligence.regime_detector import RegimeDetector


class DataFeed:
    """Fetch and enrich market data for runtime and research workflows."""

    def __init__(self, broker: BaseBroker, config: dict):
        self.broker = broker
        self.config = config

        raw_tf = config.get("data", {}).get("candle_interval", "5Min")
        self.default_timeframe = canonical_timeframe(raw_tf)
        self.timeframe = timeframe_to_ccxt(raw_tf)
        self.lookback_days = int(config.get("data", {}).get("lookback_days", 60))
        self.max_bars_per_request = int(
            config.get("data", {}).get("max_bars_per_request", 1000)
        )
        self.refresh_overlap_bars = int(
            config.get("data", {}).get("refresh_overlap_bars", 400)
        )

        self._cache: dict[str, dict[str, pd.DataFrame]] = {}
        self._last_update: dict[str, dict[str, datetime]] = {}
        self.regime_detector = RegimeDetector(config=config)
        self.indicator_pipeline = IndicatorPipeline(self.regime_detector)

    def get_data(
        self,
        symbol: str,
        force_refresh: bool = False,
        strategies: Sequence | None = None,
        timeframe: str | None = None,
    ) -> pd.DataFrame:
        """
        Fetch the default lookback window with paginated historical requests.
        """
        resolved_timeframe = self._resolved_timeframe(timeframe)
        cached = self._get_cached_frame(symbol, resolved_timeframe)

        if not force_refresh and cached is not None:
            enriched = self.indicator_pipeline.enrich(cached, strategies)
            self._set_cached_frame(symbol, resolved_timeframe, enriched)
            return enriched

        if force_refresh and cached is not None and not cached.empty:
            return self._refresh_cached_data(
                symbol,
                timeframe=resolved_timeframe,
                strategies=strategies,
            )

        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=self.lookback_days)
        return self.get_historical_range(
            symbol,
            start_date,
            end_date,
            strategies=strategies,
            cache_symbol=symbol,
            timeframe=resolved_timeframe,
        )

    def get_historical_range(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        *,
        strategies: Sequence | None = None,
        cache_symbol: str | None = None,
        timeframe: str | None = None,
    ) -> pd.DataFrame:
        """
        Fetch and enrich a specific date range using paginated broker requests.
        """
        resolved_timeframe = self._resolved_timeframe(timeframe)
        logger.info(
            f"Fetching historical range for {symbol} ({resolved_timeframe}): "
            f"{start_date} to {end_date}"
        )

        df = self._fetch_range(
            symbol,
            start_date,
            end_date,
            timeframe=resolved_timeframe,
        )
        if df.empty:
            return df

        enriched = self.indicator_pipeline.enrich(df, strategies)
        if cache_symbol:
            self._set_cached_frame(cache_symbol, resolved_timeframe, enriched)
        return enriched

    def refresh_all(
        self,
        symbols: list[str],
        strategies: Sequence | None = None,
        timeframe: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Refresh a single timeframe across the active symbol universe."""
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            try:
                result[symbol] = self.get_data(
                    symbol,
                    force_refresh=True,
                    strategies=strategies,
                    timeframe=timeframe,
                )
            except Exception as exc:
                logger.error(f"Failed to refresh {symbol}: {exc}")
                result[symbol] = pd.DataFrame()
        return result

    def refresh_for_strategies(
        self,
        symbols: list[str],
        strategies: Sequence | None = None,
    ) -> dict[str, dict[str, pd.DataFrame]]:
        """Refresh every required strategy timeframe for each symbol."""
        grouped = self._group_strategies_by_timeframe(strategies)
        result: dict[str, dict[str, pd.DataFrame]] = {}

        for symbol in symbols:
            symbol_frames: dict[str, pd.DataFrame] = {}
            for timeframe, timeframe_strategies in grouped.items():
                try:
                    symbol_frames[timeframe] = self.get_data(
                        symbol,
                        force_refresh=True,
                        strategies=timeframe_strategies,
                        timeframe=timeframe,
                    )
                except Exception as exc:
                    logger.error(
                        f"Failed to refresh {symbol} for {timeframe}: {exc}"
                    )
                    symbol_frames[timeframe] = pd.DataFrame()
            result[symbol] = symbol_frames
        return result

    def get_historical_range_for_strategies(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        *,
        strategies: Sequence | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch an explicit date range for all required strategy timeframes."""
        grouped = self._group_strategies_by_timeframe(strategies)
        result: dict[str, pd.DataFrame] = {}
        for timeframe, timeframe_strategies in grouped.items():
            result[timeframe] = self.get_historical_range(
                symbol,
                start_date,
                end_date,
                strategies=timeframe_strategies,
                timeframe=timeframe,
            )
        return result

    def primary_frame(
        self,
        data: pd.DataFrame | dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """Choose a representative frame, preferring the most granular timeframe."""
        if isinstance(data, pd.DataFrame):
            return data

        available = [
            (timeframe, frame)
            for timeframe, frame in data.items()
            if isinstance(frame, pd.DataFrame) and not frame.empty
        ]
        if not available:
            return pd.DataFrame()

        available.sort(key=lambda item: timeframe_to_minutes(item[0]))
        return available[0][1]

    def strategy_timeframe(self, strategy) -> str:
        return canonical_timeframe(
            getattr(strategy, "timeframe", None) or self.default_timeframe
        )

    def get_latest_price(
        self,
        symbol: str,
        *,
        prefer_live: bool = False,
    ) -> Optional[float]:
        """Return the latest cached close or fallback ticker price."""
        cached = self._latest_cached_frame(symbol)
        if not prefer_live and cached is not None:
            return float(cached["close"].iloc[-1])
        try:
            ticker = self.broker.fetch_ticker(symbol)
            return float(ticker.get("last", 0.0) or 0.0)
        except Exception:
            if cached is not None:
                return float(cached["close"].iloc[-1])
            return None

    def get_vwap(self, symbol: str) -> Optional[float]:
        """Return the latest cached VWAP value."""
        cached = self._latest_cached_frame(symbol)
        if cached is not None and "vwap" in cached.columns:
            return float(cached["vwap"].iloc[-1])
        return None

    def clear_cache(self):
        """Clear the local data cache."""
        self._cache.clear()
        self._last_update.clear()
        logger.debug("Data cache cleared")

    @property
    def cached_symbols(self) -> list[str]:
        return list(self._cache.keys())

    def _refresh_cached_data(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        strategies: Sequence | None = None,
    ) -> pd.DataFrame:
        resolved_timeframe = self._resolved_timeframe(timeframe)
        cached = self._get_cached_frame(symbol, resolved_timeframe)
        if cached is None or cached.empty:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=self.lookback_days)
            return self.get_historical_range(
                symbol,
                start_date,
                end_date,
                strategies=strategies,
                cache_symbol=symbol,
                timeframe=resolved_timeframe,
            )

        overlap_bars = max(50, self.refresh_overlap_bars)
        overlap_index = max(0, len(cached) - overlap_bars)
        start_date = cached.index[overlap_index].to_pydatetime()
        end_date = datetime.utcnow()

        logger.info(
            f"Refreshing cached range for {symbol} ({resolved_timeframe}): "
            f"{start_date} to {end_date}"
        )
        recent_raw = self._fetch_range(
            symbol,
            start_date,
            end_date,
            timeframe=resolved_timeframe,
        )
        if recent_raw.empty:
            return cached

        history_prefix = cached.loc[
            cached.index < start_date,
            ["open", "high", "low", "close", "volume"],
        ]
        combined = pd.concat([history_prefix, recent_raw])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)

        cutoff = end_date - timedelta(days=self.lookback_days)
        combined = combined.loc[combined.index >= cutoff]
        enriched = self.indicator_pipeline.enrich(combined, strategies)
        self._set_cached_frame(symbol, resolved_timeframe, enriched)
        return enriched

    def _fetch_range(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        timeframe: str | None = None,
    ) -> pd.DataFrame:
        all_frames: list[pd.DataFrame] = []
        since_ms = int(start_date.timestamp() * 1000)
        end_ms = int(end_date.timestamp() * 1000)
        target_tf = self._resolved_timeframe(timeframe)
        step_ms = timeframe_to_minutes(target_tf) * 60 * 1000

        while since_ms <= end_ms:
            batch = self.broker.fetch_ohlcv(
                symbol,
                timeframe=target_tf,
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
        return timeframe_to_minutes(self.default_timeframe) * 60 * 1000

    def _resolved_timeframe(self, timeframe: str | None) -> str:
        return canonical_timeframe(timeframe or self.default_timeframe)

    def _get_cached_frame(
        self,
        symbol: str,
        timeframe: str,
    ) -> pd.DataFrame | None:
        return self._cache.get(symbol, {}).get(timeframe)

    def _set_cached_frame(
        self,
        symbol: str,
        timeframe: str,
        frame: pd.DataFrame,
    ):
        self._cache.setdefault(symbol, {})[timeframe] = frame
        self._last_update.setdefault(symbol, {})[timeframe] = datetime.utcnow()

    def _latest_cached_frame(self, symbol: str) -> pd.DataFrame | None:
        frames = self._cache.get(symbol, {})
        ranked = [
            (timeframe, frame)
            for timeframe, frame in frames.items()
            if isinstance(frame, pd.DataFrame) and not frame.empty
        ]
        if not ranked:
            return None

        ranked.sort(
            key=lambda item: (
                item[1].index[-1],
                -timeframe_to_minutes(item[0]),
            )
        )
        return ranked[-1][1]

    def _group_strategies_by_timeframe(
        self,
        strategies: Sequence | None,
    ) -> dict[str, list]:
        grouped: dict[str, list] = {}
        for strategy in strategies or []:
            timeframe = self.strategy_timeframe(strategy)
            grouped.setdefault(timeframe, []).append(strategy)
        if grouped:
            return grouped
        return {self.default_timeframe: []}
