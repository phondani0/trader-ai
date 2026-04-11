"""
Market Regime Detector.

Classifies the current market environment into one of four regimes:
    - Trending + Low Volatility    → Best for trend followers (Supertrend, EMA)
    - Trending + High Volatility   → Cautious trend following (wider stops)
    - Sideways + Low Volatility    → Mean reversion paradise (MACD+BB, VWAP)
    - Sideways + High Volatility   → DANGEROUS — sit out or reduce size

Uses ADX for trend strength and ATR% for volatility classification.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from src.core.models import MarketRegime


class RegimeDetector:
    """
    Detects the current market regime for each symbol.

    Regime is determined by two axes:
        1. Trend: ADX > threshold = Trending, else Sideways
        2. Volatility: ATR% > historical avg = High Vol, else Low Vol
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.adx_trending_threshold = 25   # ADX > 25 = trending
        self.atr_lookback = 100            # Compare current ATR to 100-period average

    def detect(self, df: pd.DataFrame) -> MarketRegime:
        """
        Detect market regime from indicator-enriched DataFrame.

        Args:
            df: DataFrame with pre-computed indicators (adx, atr_pct)

        Returns:
            MarketRegime enum value
        """
        if df.empty or len(df) < 30:
            return MarketRegime.UNKNOWN

        curr = df.iloc[-1]
        adx = curr.get("adx")
        atr_pct = curr.get("atr_pct")

        if pd.isna(adx) or pd.isna(atr_pct):
            return MarketRegime.UNKNOWN

        # Determine trend
        is_trending = adx > self.adx_trending_threshold

        # Determine volatility (compare to historical average)
        atr_pct_series = df["atr_pct"].dropna()
        if len(atr_pct_series) < self.atr_lookback:
            atr_avg = atr_pct_series.mean()
        else:
            atr_avg = atr_pct_series.tail(self.atr_lookback).mean()

        is_high_vol = atr_pct > atr_avg if not pd.isna(atr_avg) else False

        # Classify
        if is_trending and not is_high_vol:
            regime = MarketRegime.TRENDING_LOW_VOL
        elif is_trending and is_high_vol:
            regime = MarketRegime.TRENDING_HIGH_VOL
        elif not is_trending and not is_high_vol:
            regime = MarketRegime.SIDEWAYS_LOW_VOL
        else:
            regime = MarketRegime.SIDEWAYS_HIGH_VOL

        return regime

    def detect_multi(self, data: dict[str, pd.DataFrame]) -> dict[str, MarketRegime]:
        """Detect regime for multiple symbols."""
        return {symbol: self.detect(df) for symbol, df in data.items()}

    def get_allowed_strategies(self, regime: MarketRegime) -> list[str]:
        """
        Get list of strategy names that should be active in this regime.

        This is the core decision matrix that protects capital by
        preventing strategies from running in hostile conditions.
        """
        strategy_map = {
            MarketRegime.TRENDING_LOW_VOL: [
                "supertrend", "ema_crossover", "rsi_divergence", "chart_patterns", "orb",
            ],
            MarketRegime.TRENDING_HIGH_VOL: [
                "supertrend", "chart_patterns", "orb",  # Fewer strategies, wider stops
            ],
            MarketRegime.SIDEWAYS_LOW_VOL: [
                "macd_bollinger", "vwap_reversion", "chart_patterns", "orb",
            ],
            MarketRegime.SIDEWAYS_HIGH_VOL: [
                "orb",  # Only ORB — most other strategies get eaten alive here
            ],
            MarketRegime.UNKNOWN: [
                "orb",  # Conservative — only the most robust strategy
            ],
        }
        return strategy_map.get(regime, [])

    def regime_summary(self, regime: MarketRegime) -> str:
        """Human-readable regime description."""
        descriptions = {
            MarketRegime.TRENDING_LOW_VOL: "📈 Trending + Calm — ideal for trend followers",
            MarketRegime.TRENDING_HIGH_VOL: "📈⚡ Trending + Volatile — use wider stops",
            MarketRegime.SIDEWAYS_LOW_VOL: "↔️ Ranging + Calm — mean reversion territory",
            MarketRegime.SIDEWAYS_HIGH_VOL: "↔️⚡ Choppy — DANGER ZONE, reduce exposure",
            MarketRegime.UNKNOWN: "❓ Unknown regime — trading conservatively",
        }
        return descriptions.get(regime, "Unknown")
