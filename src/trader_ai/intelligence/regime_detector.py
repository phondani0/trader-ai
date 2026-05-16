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

from trader_ai.core.models import MarketRegime, MarketBias, RegimeState


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
        self.bias_ema_period = 1000        # Use 1000-period EMA for macro bias (The "20-Day Wall")

    def indicator_requirements(self) -> dict[str, object]:
        return {
            "adx": [14],
            "atr": [14],
            "volume_sma": [20],
            "ema": [self.bias_ema_period],
        }

    def detect(self, df: pd.DataFrame) -> RegimeState:
        """
        Detect market regime and macro bias.
        """
        if df.empty or len(df) < 30:
            return RegimeState(regime=MarketRegime.UNKNOWN, bias=MarketBias.NEUTRAL)

        curr = df.iloc[-1]
        adx = curr.get("adx")
        atr_pct = curr.get("atr_pct")
        bias_ema_col = f"ema_{self.bias_ema_period}"
        bias_ema = curr.get(bias_ema_col)
        close = curr.get("close")

        if pd.isna(adx) or pd.isna(atr_pct):
            return RegimeState(regime=MarketRegime.UNKNOWN, bias=MarketBias.NEUTRAL)

        # Determine trend
        is_trending = adx > self.adx_trending_threshold

        # Determine volatility
        atr_pct_series = df["atr_pct"].dropna()
        atr_avg = atr_pct_series.tail(self.atr_lookback).mean() if len(atr_pct_series) >= 20 else atr_pct_series.mean()
        is_high_vol = atr_pct > atr_avg if not pd.isna(atr_avg) else False

        # Classify Regime
        if is_trending and not is_high_vol:
            regime = MarketRegime.TRENDING_LOW_VOL
        elif is_trending and is_high_vol:
            regime = MarketRegime.TRENDING_HIGH_VOL
        elif not is_trending and not is_high_vol:
            regime = MarketRegime.SIDEWAYS_LOW_VOL
        else:
            regime = MarketRegime.SIDEWAYS_HIGH_VOL

        # Determine Bias (EMA1000 - Macro Filter)
        if not pd.isna(bias_ema) and not pd.isna(close):
            if close > bias_ema * 1.01: # 1.0% buffer for Bullish Macro
                bias = MarketBias.BULLISH
            elif close < bias_ema * 0.99: # 1.0% buffer for Bearish Macro
                bias = MarketBias.BEARISH
            else:
                bias = MarketBias.NEUTRAL
        else:
            bias = MarketBias.NEUTRAL

        is_burst = False
        is_sweep = False

        if "volume_burst_ratio" in df.columns:
            is_burst = float(df["volume_burst_ratio"].iloc[-1]) > 3.0
        
        if "is_bullish_sweep" in df.columns and "is_bearish_sweep" in df.columns:
            is_sweep = bool(df["is_bullish_sweep"].iloc[-1] or df["is_bearish_sweep"].iloc[-1])

        return RegimeState(
            regime=regime,
            bias=bias,
            is_burst=is_burst,
            is_sweep=is_sweep
        )

    def profile_window(self, df: pd.DataFrame) -> dict:
        """Summarize a historical window using real market features."""
        if df.empty:
            return {
                "bars": 0,
                "dominant_regime": MarketRegime.UNKNOWN.value,
                "dominant_execution_regime": MarketRegime.UNKNOWN.value,
                "regime_mix_pct": {},
                "execution_regime_mix_pct": {},
                "detected_market_profile": "unknown",
                "market_bias": "flat",
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

        adx_col = df.get("adx")
        atr_pct_col = df.get("atr_pct")
        
        if adx_col is not None and atr_pct_col is not None:
            mask_valid = adx_col.notna() & atr_pct_col.notna() & rolling_atr_avg.notna()
            adx_valid = adx_col[mask_valid]
            atr_pct_valid = atr_pct_col[mask_valid]
            atr_avg_valid = rolling_atr_avg[mask_valid]
            
            is_trending = adx_valid > self.adx_trending_threshold
            is_high_vol = atr_pct_valid > atr_avg_valid
            
            regime_counts[MarketRegime.TRENDING_LOW_VOL.value] = int((is_trending & ~is_high_vol).sum())
            regime_counts[MarketRegime.TRENDING_HIGH_VOL.value] = int((is_trending & is_high_vol).sum())
            regime_counts[MarketRegime.SIDEWAYS_LOW_VOL.value] = int((~is_trending & ~is_high_vol).sum())
            regime_counts[MarketRegime.SIDEWAYS_HIGH_VOL.value] = int((~is_trending & is_high_vol).sum())

        total_regime_obs = sum(regime_counts.values())
        dominant_regime = (
            regime_counts.most_common(1)[0][0]
            if total_regime_obs
            else MarketRegime.UNKNOWN.value
        )
        regime_mix_pct = (
            {
                regime: (count / total_regime_obs) * 100
                for regime, count in regime_counts.items()
                if count > 0
            }
            if total_regime_obs
            else {}
        )

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

        detected_market_profile = self.classify_market_profile(
            window_return_pct=window_return_pct,
            realized_vol_pct=realized_vol_pct,
            trend_efficiency_pct=trend_efficiency_pct,
            breakout_rate_pct=breakout_rate_pct,
            median_adx=float(adx_series.median()) if not adx_series.empty else 0.0,
        )

        market_bias = "flat"
        if window_return_pct > 5:
            market_bias = "bullish"
        elif window_return_pct < -5:
            market_bias = "bearish"

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
            "dominant_execution_regime": dominant_regime,
            "regime_mix_pct": regime_mix_pct,
            "execution_regime_mix_pct": regime_mix_pct,
            "detected_market_profile": detected_market_profile,
            "market_bias": market_bias,
        }

    def classify_market_profile(
        self,
        *,
        window_return_pct: float,
        realized_vol_pct: float,
        trend_efficiency_pct: float,
        breakout_rate_pct: float,
        median_adx: float,
    ) -> str:
        """Classify a whole benchmark window into a macro market profile."""
        abs_return = abs(window_return_pct)

        if (
            abs_return >= 20
            and trend_efficiency_pct >= 1.0
            and (breakout_rate_pct >= 9.0 or realized_vol_pct >= 18.0)
        ):
            return "breakout"

        if abs_return >= 12 and (trend_efficiency_pct >= 0.9 or median_adx >= 24):
            return "trend"

        if abs_return <= 10 and median_adx < self.adx_trending_threshold:
            return "range"

        return "mixed"

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
                "supertrend",
                "ema_crossover",
                "rsi_divergence",
                "donchian_breakout",
                "bearish_breakdown_continuation",
                "episodic_pivot",
                "episodic_pivot_short",
                "volume_weighted_tsmom",
                "chart_patterns",
                "orb",
            ],
            MarketRegime.TRENDING_HIGH_VOL: [
                "supertrend",
                "rsi_divergence",
                "donchian_breakout",
                "bearish_breakdown_continuation",
                "episodic_pivot",
                "episodic_pivot_short",
                "volume_weighted_tsmom",
                "chart_patterns",
                "orb",
            ],
            MarketRegime.SIDEWAYS_LOW_VOL: [
                "macd_bollinger",
                "vwap_reversion",
                "range_reversion",
                "episodic_pivot",
                "episodic_pivot_short",
                "chart_patterns",
                "orb",
            ],
            MarketRegime.SIDEWAYS_HIGH_VOL: [
                "range_reversion",
                "episodic_pivot",
                "episodic_pivot_short",
                "orb",
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
