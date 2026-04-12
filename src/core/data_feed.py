"""
Data Feed Manager.

Manages historical data fetching, caching, and provides
clean DataFrames with technical indicators pre-computed.

Uses pandas-ta for all technical indicator calculations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd
import pandas_ta as ta
from loguru import logger

from src.core.base_broker import BaseBroker


# ─── Internal Timeframe Mapping ───────────────────────────────────────
# Config uses "5Min" style, brokers use "5m" style. Translate here once.

_TIMEFRAME_MAP = {
    "1Min": "1m", "5Min": "5m", "15Min": "15m",
    "30Min": "30m", "1Hour": "1h", "1Day": "1d",
}


class DataFeed:
    """
    Central data manager that fetches, caches, and enriches market data
    with technical indicators needed by all strategies.
    """

    def __init__(self, broker: BaseBroker, config: dict):
        self.broker = broker
        self.config = config

        raw_tf = config.get("data", {}).get("candle_interval", "5Min")
        self.timeframe = _TIMEFRAME_MAP.get(raw_tf, raw_tf)
        self.lookback_days = config.get("data", {}).get("lookback_days", 60)

        # Cache: symbol -> DataFrame (refreshed every candle)
        self._cache: dict[str, pd.DataFrame] = {}
        self._last_update: dict[str, datetime] = {}

    def get_data(self, symbol: str, force_refresh: bool = False) -> pd.DataFrame:
        """
        Get enriched OHLCV data with all technical indicators pre-computed.

        Returns DataFrame with columns:
            open, high, low, close, volume, vwap,
            ema_9, ema_21, sma_20,
            rsi_14, macd, macd_signal, macd_hist,
            bb_upper, bb_mid, bb_lower,
            adx, atr, atr_pct,
            supertrend, supertrend_direction,
            volume_sma_20
        """
        if not force_refresh and symbol in self._cache:
            return self._cache[symbol]

        try:
            # Estimate bar limit from lookback days and timeframe
            minutes_map = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
            bar_minutes = minutes_map.get(self.timeframe, 5)
            limit = min(self.lookback_days * (1440 // bar_minutes), 1000)

            df = self.broker.fetch_ohlcv(symbol, self.timeframe, limit=limit)

            if df.empty:
                logger.warning(f"No data for {symbol}")
                return df

            df = self._add_indicators(df)
            self._cache[symbol] = df
            self._last_update[symbol] = datetime.utcnow()

            return df

        except Exception as e:
            logger.error(f"Failed to get data for {symbol}: {e}")
            return pd.DataFrame()

    def refresh_all(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        """Refresh data for all symbols in the watchlist."""
        result = {}
        for symbol in symbols:
            try:
                df = self.get_data(symbol, force_refresh=True)
                result[symbol] = df
            except Exception as e:
                logger.error(f"Failed to refresh {symbol}: {e}")
                result[symbol] = pd.DataFrame()
        return result

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get the latest price for a symbol from cached data."""
        if symbol in self._cache and not self._cache[symbol].empty:
            return float(self._cache[symbol]["close"].iloc[-1])
        try:
            ticker = self.broker.fetch_ticker(symbol)
            return ticker.get("last", 0.0)
        except Exception:
            return None

    def get_vwap(self, symbol: str) -> Optional[float]:
        """Get current VWAP for a symbol."""
        if symbol in self._cache and not self._cache[symbol].empty:
            df = self._cache[symbol]
            if "vwap" in df.columns:
                return float(df["vwap"].iloc[-1])
        return None

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute all technical indicators needed by the strategy engine.
        This runs once per data refresh — all strategies read from this.
        """
        if df.empty or len(df) < 30:
            return df

        # ─── Moving Averages ──────────────────────────────────────
        df["ema_9"] = ta.ema(df["close"], length=9)
        df["ema_21"] = ta.ema(df["close"], length=21)
        df["sma_20"] = ta.sma(df["close"], length=20)
        df["sma_50"] = ta.sma(df["close"], length=50)

        # ─── RSI ──────────────────────────────────────────────────
        df["rsi_14"] = ta.rsi(df["close"], length=14)

        # ─── MACD ─────────────────────────────────────────────────
        macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd is not None and not macd.empty:
            df["macd"] = macd.iloc[:, 0]
            df["macd_hist"] = macd.iloc[:, 1]
            df["macd_signal"] = macd.iloc[:, 2]

        # ─── Bollinger Bands ──────────────────────────────────────
        bbands = ta.bbands(df["close"], length=20, std=2.0)
        if bbands is not None and not bbands.empty:
            df["bb_lower"] = bbands.iloc[:, 0]
            df["bb_mid"] = bbands.iloc[:, 1]
            df["bb_upper"] = bbands.iloc[:, 2]
            df["bb_bandwidth"] = bbands.iloc[:, 3] if bbands.shape[1] > 3 else None
            df["bb_pct"] = bbands.iloc[:, 4] if bbands.shape[1] > 4 else None

        # ─── ADX (Trend Strength) ─────────────────────────────────
        adx = ta.adx(df["high"], df["low"], df["close"], length=14)
        if adx is not None and not adx.empty:
            df["adx"] = adx.iloc[:, 0]
            df["di_plus"] = adx.iloc[:, 1]
            df["di_minus"] = adx.iloc[:, 2]

        # ─── ATR (Volatility) ─────────────────────────────────────
        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
        df["atr_pct"] = (df["atr"] / df["close"]) * 100

        # ─── Supertrend ───────────────────────────────────────────
        supertrend = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=3.0)
        if supertrend is not None and not supertrend.empty:
            df["supertrend"] = supertrend.iloc[:, 0]
            df["supertrend_dir"] = supertrend.iloc[:, 1]

        # ─── Volume ───────────────────────────────────────────────
        df["volume_sma_20"] = ta.sma(df["volume"], length=20)
        df["volume_ratio"] = df["volume"] / df["volume_sma_20"]

        # ─── VWAP (intraday) ──────────────────────────────────────
        if "vwap" not in df.columns or df["vwap"].isna().all():
            df["vwap"] = ta.vwap(df["high"], df["low"], df["close"], df["volume"])

        # ─── Stochastic RSI ──────────────────────────────────────
        stoch_rsi = ta.stochrsi(df["close"], length=14)
        if stoch_rsi is not None and not stoch_rsi.empty:
            df["stoch_rsi_k"] = stoch_rsi.iloc[:, 0]
            df["stoch_rsi_d"] = stoch_rsi.iloc[:, 1]

        return df

    def clear_cache(self):
        """Clear all cached data."""
        self._cache.clear()
        self._last_update.clear()
        logger.debug("Data cache cleared")

    @property
    def cached_symbols(self) -> list[str]:
        """List of symbols currently in cache."""
        return list(self._cache.keys())
