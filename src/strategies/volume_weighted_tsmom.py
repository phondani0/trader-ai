"""
Strategy: Volume-Weighted Time-Series Momentum

Research basis:
    - Crypto time-series momentum is more robust than cross-sectional momentum.
    - Momentum quality improves when it is filtered for real participation and
      directional follow-through rather than traded as a naked ROC signal.

Inference applied here:
    - Use multiple momentum horizons
    - Weight momentum by relative volume
    - Only trade fresh range breaks in the direction of the prevailing trend
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import MarketRegime, Signal, SignalType, Side, StrategyType
from src.strategies.base import BaseStrategy


class VolumeWeightedTSMomentumStrategy(BaseStrategy):
    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

    @property
    def name(self) -> str:
        return "volume_weighted_tsmom"

    def indicator_requirements(self) -> dict[str, object]:
        trend_ema_period = int(self.params.get("trend_ema_period", 200))
        confirm_ema_period = int(self.params.get("confirm_ema_period", 50))
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))
        volume_period = int(self.params.get("volume_period", 20))
        return {
            "ema": [trend_ema_period, confirm_ema_period],
            "atr": [atr_period],
            "adx": [adx_period],
            "volume_sma": [volume_period],
        }

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        fast_momentum_bars = int(self.params.get("fast_momentum_bars", 12))
        slow_momentum_bars = int(self.params.get("slow_momentum_bars", 48))
        breakout_lookback = int(self.params.get("breakout_lookback", 20))
        pullback_lookback = int(self.params.get("pullback_lookback", 10))
        trend_ema_period = int(self.params.get("trend_ema_period", 200))
        confirm_ema_period = int(self.params.get("confirm_ema_period", 50))
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))
        volume_period = int(self.params.get("volume_period", 20))
        slope_lookback = int(self.params.get("slope_lookback", 12))

        min_bars = max(
            slow_momentum_bars + 2,
            breakout_lookback + 2,
            pullback_lookback + 2,
            trend_ema_period + slope_lookback + 2,
            80,
        )
        if len(df) < min_bars:
            return None

        trend_col = self.ema_col(trend_ema_period)
        confirm_col = self.ema_col(confirm_ema_period)
        atr_col = self.atr_col(atr_period)
        atr_pct_col = self.atr_pct_col(atr_period)
        adx_cols = self.adx_cols(adx_period)
        volume_ratio_col = self.volume_ratio_col(volume_period)

        required = [
            "close",
            "high",
            "low",
            trend_col,
            confirm_col,
            atr_col,
            atr_pct_col,
            adx_cols["adx"],
            volume_ratio_col,
        ]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        slope_ref = df.iloc[-1 - slope_lookback]
        recent = df.iloc[-pullback_lookback - 1 : -1]

        close = float(curr["close"])
        prev_close = float(prev["close"])
        trend_ema = curr[trend_col]
        confirm_ema = curr[confirm_col]
        trend_ema_prev = slope_ref[trend_col]
        atr = curr[atr_col]
        atr_pct = curr[atr_pct_col]
        adx = curr[adx_cols["adx"]]
        di_plus = curr.get(adx_cols["di_plus"])
        di_minus = curr.get(adx_cols["di_minus"])
        volume_ratio = curr[volume_ratio_col]

        if any(
            pd.isna(value)
            for value in [trend_ema, confirm_ema, trend_ema_prev, atr, atr_pct, adx]
        ):
            return None
        if atr <= 0 or atr_pct <= 0:
            return None

        volume_ratio = 1.0 if pd.isna(volume_ratio) else float(volume_ratio)
        adx = float(adx)
        di_plus = None if pd.isna(di_plus) else float(di_plus)
        di_minus = None if pd.isna(di_minus) else float(di_minus)

        fast_ref = float(df["close"].iloc[-1 - fast_momentum_bars])
        slow_ref = float(df["close"].iloc[-1 - slow_momentum_bars])
        if fast_ref <= 0 or slow_ref <= 0:
            return None

        fast_return_pct = ((close / fast_ref) - 1) * 100
        slow_return_pct = ((close / slow_ref) - 1) * 100
        weighted_momentum = (
            (fast_return_pct * 0.65) + (slow_return_pct * 0.35)
        ) * max(1.0, volume_ratio)
        normalized_momentum = weighted_momentum / max(float(atr_pct), 0.05)

        adx_min = float(self.params.get("adx_min", 18))
        volume_multiplier = float(self.params.get("volume_multiplier", 1.05))
        min_normalized_momentum = float(
            self.params.get("min_normalized_momentum", 18.0)
        )
        breakout_buffer_atr = float(self.params.get("breakout_buffer_atr", 0.05))
        stop_buffer_atr = float(self.params.get("stop_buffer_atr", 1.0))
        pullback_touch_atr = float(self.params.get("pullback_touch_atr", 0.6))
        take_profit_rr_ratio = float(self.params.get("take_profit_rr_ratio", 3.0))

        breakout_high = float(
            df["high"].shift(1).rolling(breakout_lookback).max().iloc[-1]
        )
        breakout_low = float(
            df["low"].shift(1).rolling(breakout_lookback).min().iloc[-1]
        )
        if pd.isna(breakout_high) or pd.isna(breakout_low):
            return None

        breakout_buffer = atr * breakout_buffer_atr
        recent_low = float(recent["low"].min())
        recent_high = float(recent["high"].max())
        long_pullback_seen = recent_low <= (float(confirm_ema) + (atr * pullback_touch_atr))
        short_pullback_seen = recent_high >= (float(confirm_ema) - (atr * pullback_touch_atr))

        long_setup = (
            close > confirm_ema > trend_ema
            and trend_ema > trend_ema_prev
            and adx >= adx_min
            and volume_ratio >= volume_multiplier
            and normalized_momentum >= min_normalized_momentum
            and prev_close <= (breakout_high + breakout_buffer)
            and close > (breakout_high + breakout_buffer)
            and long_pullback_seen
            and (di_plus is None or di_minus is None or di_plus > di_minus)
        )
        if long_setup:
            stop_anchor = min(recent_low, float(confirm_ema))
            stop_loss = stop_anchor - (atr * stop_buffer_atr)
            risk = close - stop_loss
            if risk <= 0:
                return None
            take_profit = close + (risk * take_profit_rr_ratio)
            confidence = min(
                0.95,
                0.62
                + min(0.15, max(0.0, normalized_momentum - min_normalized_momentum) / 100)
                + min(0.08, max(0.0, volume_ratio - volume_multiplier))
                + min(0.08, max(0.0, adx - adx_min) / 100),
            )
            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                side=Side.BUY,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                regime=regime,
                reason=(
                    f"VW-TSMOM long | norm_mom={normalized_momentum:.1f} "
                    f"| ADX {adx:.1f} | VolRatio {volume_ratio:.2f}"
                ),
                metadata={
                    "fast_return_pct": fast_return_pct,
                    "slow_return_pct": slow_return_pct,
                    "weighted_momentum": weighted_momentum,
                    "normalized_momentum": normalized_momentum,
                    "adx": adx,
                    "volume_ratio": volume_ratio,
                },
            )

        short_setup = (
            close < confirm_ema < trend_ema
            and trend_ema < trend_ema_prev
            and adx >= adx_min
            and volume_ratio >= volume_multiplier
            and normalized_momentum <= -min_normalized_momentum
            and prev_close >= (breakout_low - breakout_buffer)
            and close < (breakout_low - breakout_buffer)
            and short_pullback_seen
            and (di_plus is None or di_minus is None or di_minus > di_plus)
        )
        if short_setup:
            stop_anchor = max(recent_high, float(confirm_ema))
            stop_loss = stop_anchor + (atr * stop_buffer_atr)
            risk = stop_loss - close
            if risk <= 0:
                return None
            take_profit = close - (risk * take_profit_rr_ratio)
            confidence = min(
                0.95,
                0.62
                + min(0.15, max(0.0, abs(normalized_momentum) - min_normalized_momentum) / 100)
                + min(0.08, max(0.0, volume_ratio - volume_multiplier))
                + min(0.08, max(0.0, adx - adx_min) / 100),
            )
            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                side=Side.SELL,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                regime=regime,
                reason=(
                    f"VW-TSMOM short | norm_mom={normalized_momentum:.1f} "
                    f"| ADX {adx:.1f} | VolRatio {volume_ratio:.2f}"
                ),
                metadata={
                    "fast_return_pct": fast_return_pct,
                    "slow_return_pct": slow_return_pct,
                    "weighted_momentum": weighted_momentum,
                    "normalized_momentum": normalized_momentum,
                    "adx": adx,
                    "volume_ratio": volume_ratio,
                },
            )

        return None
