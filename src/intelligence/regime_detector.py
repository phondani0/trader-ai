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

from collections import Counter

import pandas as pd

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

    def indicator_requirements(self) -> dict[str, object]:
        return {
            "adx": [14],
            "atr": [14],
            "volume_sma": [20],
        }

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

    def profile_window(self, df: pd.DataFrame) -> dict:
        """Summarize a historical window using real market features."""
        if df.empty:
            return {
                "bars": 0,
                "dominant_regime": MarketRegime.UNKNOWN.value,
                "regime_mix_pct": {},
            }

        closes = df["close"].dropna()
        returns = closes.pct_change().dropna()
        close_start = float(closes.iloc[0]) if not closes.empty else 0.0
        close_end = float(closes.iloc[-1]) if not closes.empty else 0.0

        adx_series = df.get("adx", pd.Series(dtype=float)).dropna()
        atr_pct_series = df.get("atr_pct", pd.Series(dtype=float)).dropna()
        volume_ratio = df.get("volume_ratio", pd.Series(dtype=float)).dropna()

        rolling_atr_avg = df.get("atr_pct", pd.Series(dtype=float)).rolling(
            self.atr_lookback, min_periods=20
        ).mean()
        regime_counts: Counter = Counter()

        for idx in df.index:
            adx = df.at[idx, "adx"] if "adx" in df.columns else float("nan")
            atr_pct = df.at[idx, "atr_pct"] if "atr_pct" in df.columns else float("nan")
            atr_avg = rolling_atr_avg.loc[idx] if idx in rolling_atr_avg.index else float("nan")
            if pd.isna(adx) or pd.isna(atr_pct) or pd.isna(atr_avg):
                continue
            is_trending = adx > self.adx_trending_threshold
            is_high_vol = atr_pct > atr_avg
            if is_trending and not is_high_vol:
                regime_counts[MarketRegime.TRENDING_LOW_VOL.value] += 1
            elif is_trending and is_high_vol:
                regime_counts[MarketRegime.TRENDING_HIGH_VOL.value] += 1
            elif not is_trending and not is_high_vol:
                regime_counts[MarketRegime.SIDEWAYS_LOW_VOL.value] += 1
            else:
                regime_counts[MarketRegime.SIDEWAYS_HIGH_VOL.value] += 1

        total_regime_obs = sum(regime_counts.values())
        dominant_regime = (
            regime_counts.most_common(1)[0][0]
            if total_regime_obs
            else MarketRegime.UNKNOWN.value
        )
        regime_mix_pct = {
            regime: (count / total_regime_obs) * 100
            for regime, count in regime_counts.items()
        }

        breakout_rate_pct = 0.0
        if len(df) >= 21:
            rolling_high = df["high"].shift(1).rolling(20).max()
            rolling_low = df["low"].shift(1).rolling(20).min()
            breakouts = (
                (df["close"] > rolling_high) | (df["close"] < rolling_low)
            ).dropna()
            if not breakouts.empty:
                breakout_rate_pct = float(breakouts.mean() * 100)

        trend_efficiency_pct = 0.0
        if len(closes) > 1:
            path_length = closes.diff().abs().sum()
            if path_length > 0:
                trend_efficiency_pct = float(
                    abs(close_end - close_start) / path_length * 100
                )

        realized_vol_pct = 0.0
        if not returns.empty:
            realized_vol_pct = float(returns.std() * (len(returns) ** 0.5) * 100)

        window_return_pct = 0.0
        if close_start > 0:
            window_return_pct = ((close_end / close_start) - 1) * 100

        return {
            "bars": int(len(df)),
            "window_return_pct": float(window_return_pct),
            "realized_vol_pct": realized_vol_pct,
            "median_adx": float(adx_series.median()) if not adx_series.empty else 0.0,
            "median_atr_pct": (
                float(atr_pct_series.median()) if not atr_pct_series.empty else 0.0
            ),
            "median_volume_ratio": (
                float(volume_ratio.median()) if not volume_ratio.empty else 0.0
            ),
            "breakout_rate_pct": breakout_rate_pct,
            "trend_efficiency_pct": trend_efficiency_pct,
            "dominant_regime": dominant_regime,
            "regime_mix_pct": regime_mix_pct,
        }

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
