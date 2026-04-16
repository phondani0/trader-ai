"""
Strategy: Bearish Breakdown Continuation

Research basis:
    - Bear-market crypto often trends through failed relief rallies rather than
      clean one-bar capitulation breaks.
    - A dedicated short sleeve should wait for bearish trend structure, a bounce
      into resistance, and then a fresh breakdown/rejection instead of reusing
      bullish breakout assumptions.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import MarketRegime, Signal, SignalType, Side, StrategyType
from src.strategies.base import BaseStrategy


class BearishBreakdownContinuationStrategy(BaseStrategy):
    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

    @property
    def name(self) -> str:
        return "bearish_breakdown_continuation"

    def indicator_requirements(self) -> dict[str, object]:
        trend_ema_period = int(self.params.get("trend_ema_period", 89))
        confirm_ema_period = int(self.params.get("confirm_ema_period", 21))
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))
        volume_period = int(self.params.get("volume_period", 20))
        return {
            "ema": sorted({trend_ema_period, confirm_ema_period}),
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
        breakdown_periods = sorted(
            {
                int(period)
                for period in self.params.get("breakdown_periods", [20, 55])
            }
        )
        if not breakdown_periods:
            breakdown_periods = [20, 55]

        shortest_period = breakdown_periods[0]
        longest_period = breakdown_periods[-1]
        trend_ema_period = int(self.params.get("trend_ema_period", 89))
        confirm_ema_period = int(self.params.get("confirm_ema_period", 21))
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))
        volume_period = int(self.params.get("volume_period", 20))
        slope_lookback = int(self.params.get("slope_lookback", 8))
        momentum_lookback = int(self.params.get("momentum_lookback", shortest_period))
        rejection_lookback = int(self.params.get("rejection_lookback", 20))

        min_bars = max(
            longest_period + 2,
            momentum_lookback + 2,
            rejection_lookback + 2,
            trend_ema_period + slope_lookback + 2,
            80,
        )
        if len(df) < min_bars:
            return None

        trend_col = self.ema_col(trend_ema_period)
        confirm_col = self.ema_col(confirm_ema_period)
        atr_col = self.atr_col(atr_period)
        adx_cols = self.adx_cols(adx_period)
        volume_ratio_col = self.volume_ratio_col(volume_period)

        required = [
            "close",
            "high",
            "low",
            trend_col,
            confirm_col,
            atr_col,
            adx_cols["adx"],
            volume_ratio_col,
        ]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        slope_ref = df.iloc[-1 - slope_lookback]

        close = float(curr["close"])
        prev_close = float(prev["close"])
        atr = float(curr[atr_col])
        adx = curr[adx_cols["adx"]]
        di_plus = curr.get(adx_cols["di_plus"])
        di_minus = curr.get(adx_cols["di_minus"])
        trend_ema = curr[trend_col]
        confirm_ema = curr[confirm_col]
        trend_ema_prev = slope_ref[trend_col]
        volume_ratio = curr[volume_ratio_col]

        if any(
            pd.isna(value)
            for value in [atr, adx, trend_ema, confirm_ema, trend_ema_prev]
        ):
            return None
        if atr <= 0:
            return None

        adx = float(adx)
        volume_ratio = 1.0 if pd.isna(volume_ratio) else float(volume_ratio)
        di_plus = None if pd.isna(di_plus) else float(di_plus)
        di_minus = None if pd.isna(di_minus) else float(di_minus)

        adx_min = float(self.params.get("adx_min", 18))
        volume_multiplier = float(self.params.get("volume_multiplier", 1.0))
        min_momentum_pct = float(self.params.get("min_momentum_pct", 1.5))
        min_confirmations = int(self.params.get("min_confirmations", 2))
        breakdown_buffer_atr = float(self.params.get("breakdown_buffer_atr", 0.05))
        stop_buffer_atr = float(self.params.get("stop_buffer_atr", 1.0))
        take_profit_rr_ratio = float(self.params.get("take_profit_rr_ratio", 3.0))
        secondary_confirmations_required = int(
            self.params.get("secondary_confirmations_required", 2)
        )
        continuation_touch_atr = float(
            self.params.get("continuation_touch_atr", 0.8)
        )
        continuation_trigger_bars = int(
            self.params.get("continuation_trigger_bars", 3)
        )
        continuation_buffer_atr = float(
            self.params.get("continuation_buffer_atr", 0.05)
        )

        lower_channels: dict[int, float] = {}
        prev_lower_shortest = None
        for period in breakdown_periods:
            lower = df["low"].shift(1).rolling(period).min()
            lower_channels[period] = float(lower.iloc[-1])
            if period == shortest_period:
                prev_lower_shortest = float(lower.iloc[-2])

        if prev_lower_shortest is None or any(
            pd.isna(value) for value in lower_channels.values()
        ):
            return None

        recent_rejection_high = float(
            df["high"].shift(1).rolling(rejection_lookback).max().iloc[-1]
        )
        if pd.isna(recent_rejection_high):
            return None

        momentum_ref = float(df["close"].iloc[-1 - momentum_lookback])
        if momentum_ref <= 0:
            return None
        momentum_pct = ((close / momentum_ref) - 1) * 100

        breakdown_buffer = atr * breakdown_buffer_atr
        confirmations = sum(
            close < (channel - breakdown_buffer)
            for channel in lower_channels.values()
        )

        base_trend_ok = (
            close < confirm_ema < trend_ema
            and trend_ema < trend_ema_prev
            and adx >= adx_min
        )
        secondary_confirmations = sum(
            [
                volume_ratio >= volume_multiplier,
                momentum_pct <= -min_momentum_pct,
                di_plus is None or di_minus is None or di_minus > di_plus,
            ]
        )
        trend_ok = (
            base_trend_ok
            and secondary_confirmations >= secondary_confirmations_required
        )

        fresh_breakdown = (
            prev_close >= (prev_lower_shortest - breakdown_buffer)
            and close < (lower_channels[shortest_period] - breakdown_buffer)
        )

        rejection_window = df.iloc[-rejection_lookback - 1 : -1]
        continuation_low = float(
            rejection_window["low"].tail(continuation_trigger_bars).min()
        )
        continuation_buffer = atr * continuation_buffer_atr
        rejection_seen = (
            not rejection_window.empty
            and (
                rejection_window["high"]
                >= (float(confirm_ema) - (atr * continuation_touch_atr))
            ).any()
        )
        continuation_breakdown = (
            rejection_seen
            and prev_close >= (continuation_low - continuation_buffer)
            and close < (continuation_low - continuation_buffer)
        )

        if not trend_ok:
            return None

        if not (
            (confirmations >= min_confirmations or continuation_breakdown)
            and (fresh_breakdown or continuation_breakdown)
        ):
            return None

        stop_anchor = max(recent_rejection_high, float(confirm_ema))
        stop_loss = stop_anchor + (atr * stop_buffer_atr)
        risk = stop_loss - close
        if risk <= 0:
            return None

        take_profit = close - (risk * take_profit_rr_ratio)
        confidence = min(
            0.95,
            0.64
            + (0.05 * confirmations)
            + min(0.10, max(0.0, adx - adx_min) / 100)
            + min(0.08, max(0.0, volume_ratio - volume_multiplier)),
        )
        entry_mode = (
            "continuation"
            if continuation_breakdown and not fresh_breakdown
            else "breakdown"
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
                f"Bearish {entry_mode} short | confirmations={confirmations}/{len(breakdown_periods)} "
                f"| ADX {adx:.1f} | Momentum {momentum_pct:.1f}%"
            ),
            metadata={
                "confirmations": confirmations,
                "breakdown_periods": breakdown_periods,
                "entry_mode": entry_mode,
                "secondary_confirmations": secondary_confirmations,
                "trend_ema": float(trend_ema),
                "confirm_ema": float(confirm_ema),
                "momentum_pct": momentum_pct,
                "adx": adx,
                "volume_ratio": volume_ratio,
            },
        )
