"""
Strategy: Donchian Ensemble Breakout

Research basis:
    - Donchian-style trend following remains one of the cleaner ways to express
      time-series momentum in crypto.
    - We require multiple breakout confirmations plus directional filters so the
      strategy behaves like a high-conviction trend system instead of a naive
      single-channel breakout.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import MarketRegime, Signal, SignalType, Side
from src.strategies.base import BaseStrategy


class DonchianBreakoutStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "donchian_breakout"

    def indicator_requirements(self) -> dict[str, object]:
        trend_ema_period = int(self.params.get("trend_ema_period", 200))
        confirm_ema_period = int(self.params.get("confirm_ema_period", 50))
        short_trend_ema_period = int(
            self.params.get("short_trend_ema_period", trend_ema_period)
        )
        short_confirm_ema_period = int(
            self.params.get("short_confirm_ema_period", confirm_ema_period)
        )
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))
        volume_period = int(self.params.get("volume_period", 20))
        return {
            "ema": sorted(
                {
                    trend_ema_period,
                    confirm_ema_period,
                    short_trend_ema_period,
                    short_confirm_ema_period,
                }
            ),
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
        breakout_periods = sorted(
            {
                int(period)
                for period in self.params.get("breakout_periods", [20, 55])
            }
        )
        if not breakout_periods:
            breakout_periods = [20, 55]

        shortest_period = breakout_periods[0]
        longest_period = breakout_periods[-1]
        stop_channel_period = int(
            self.params.get("stop_channel_period", shortest_period)
        )
        momentum_lookback = int(self.params.get("momentum_lookback", shortest_period))
        trend_ema_period = int(self.params.get("trend_ema_period", 200))
        confirm_ema_period = int(self.params.get("confirm_ema_period", 50))
        short_trend_ema_period = int(
            self.params.get("short_trend_ema_period", trend_ema_period)
        )
        short_confirm_ema_period = int(
            self.params.get("short_confirm_ema_period", confirm_ema_period)
        )
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))
        volume_period = int(self.params.get("volume_period", 20))
        slope_lookback = int(self.params.get("slope_lookback", 12))
        short_slope_lookback = int(
            self.params.get("short_slope_lookback", slope_lookback)
        )

        base_required_bars = max(
            longest_period + 2,
            momentum_lookback + 2,
            stop_channel_period + 2,
            80,
        )
        long_required_bars = max(
            base_required_bars,
            trend_ema_period + slope_lookback + 2,
        )
        short_required_bars = max(
            base_required_bars,
            short_trend_ema_period + short_slope_lookback + 2,
        )
        if len(df) < min(long_required_bars, short_required_bars):
            return None

        trend_col = self.ema_col(trend_ema_period)
        confirm_col = self.ema_col(confirm_ema_period)
        short_trend_col = self.ema_col(short_trend_ema_period)
        short_confirm_col = self.ema_col(short_confirm_ema_period)
        atr_col = self.atr_col(atr_period)
        adx_cols = self.adx_cols(adx_period)
        volume_ratio_col = self.volume_ratio_col(volume_period)

        required = [
            "close",
            "high",
            "low",
            trend_col,
            confirm_col,
            short_trend_col,
            short_confirm_col,
            atr_col,
            adx_cols["adx"],
            volume_ratio_col,
        ]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        long_context_ready = len(df) >= long_required_bars
        short_context_ready = len(df) >= short_required_bars
        slope_ref = df.iloc[-1 - slope_lookback] if long_context_ready else None
        short_slope_ref = (
            df.iloc[-1 - short_slope_lookback] if short_context_ready else None
        )

        close = float(curr["close"])
        prev_close = float(prev["close"])
        atr = float(curr[atr_col])
        adx = curr[adx_cols["adx"]]
        di_plus = curr.get(adx_cols["di_plus"])
        di_minus = curr.get(adx_cols["di_minus"])
        trend_ema = curr[trend_col]
        confirm_ema = curr[confirm_col]
        trend_ema_prev = slope_ref[trend_col] if slope_ref is not None else float("nan")
        short_trend_ema = curr[short_trend_col]
        short_confirm_ema = curr[short_confirm_col]
        short_trend_ema_prev = (
            short_slope_ref[short_trend_col]
            if short_slope_ref is not None
            else float("nan")
        )
        volume_ratio = curr[volume_ratio_col]

        if any(
            pd.isna(value)
            for value in [
                atr,
                adx,
                trend_ema,
                confirm_ema,
                short_trend_ema,
                short_confirm_ema,
            ]
        ):
            return None
        if atr <= 0:
            return None

        volume_ratio = 1.0 if pd.isna(volume_ratio) else float(volume_ratio)
        adx = float(adx)
        di_plus = None if pd.isna(di_plus) else float(di_plus)
        di_minus = None if pd.isna(di_minus) else float(di_minus)

        breakout_buffer_atr = float(self.params.get("breakout_buffer_atr", 0.1))
        breakout_buffer = atr * breakout_buffer_atr
        adx_min = float(self.params.get("adx_min", 20))
        short_adx_min = float(self.params.get("short_adx_min", adx_min))
        volume_multiplier = float(self.params.get("volume_multiplier", 1.0))
        short_volume_multiplier = float(
            self.params.get("short_volume_multiplier", volume_multiplier)
        )
        min_confirmations = int(self.params.get("min_confirmations", 2))
        min_momentum_pct = float(self.params.get("min_momentum_pct", 2.0))
        short_min_momentum_pct = float(
            self.params.get("short_min_momentum_pct", min_momentum_pct)
        )
        stop_buffer_atr = float(self.params.get("stop_buffer_atr", 1.0))
        take_profit_rr_ratio = float(self.params.get("take_profit_rr_ratio", 3.0))
        allow_continuation_entries = bool(
            self.params.get("allow_continuation_entries", True)
        )
        continuation_pullback_lookback = int(
            self.params.get("continuation_pullback_lookback", 20)
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

        upper_channels: dict[int, float] = {}
        lower_channels: dict[int, float] = {}
        prev_upper_shortest = None
        prev_lower_shortest = None

        for period in breakout_periods:
            upper = df["high"].shift(1).rolling(period).max()
            lower = df["low"].shift(1).rolling(period).min()
            upper_channels[period] = float(upper.iloc[-1])
            lower_channels[period] = float(lower.iloc[-1])
            if period == shortest_period:
                prev_upper_shortest = float(upper.iloc[-2])
                prev_lower_shortest = float(lower.iloc[-2])

        if (
            prev_upper_shortest is None
            or prev_lower_shortest is None
            or any(pd.isna(v) for v in upper_channels.values())
            or any(pd.isna(v) for v in lower_channels.values())
        ):
            return None

        stop_low = float(
            df["low"].shift(1).rolling(stop_channel_period).min().iloc[-1]
        )
        stop_high = float(
            df["high"].shift(1).rolling(stop_channel_period).max().iloc[-1]
        )
        if pd.isna(stop_low) or pd.isna(stop_high):
            return None

        momentum_ref = float(df["close"].iloc[-1 - momentum_lookback])
        if momentum_ref <= 0:
            return None
        momentum_pct = ((close / momentum_ref) - 1) * 100

        retest_window = df.iloc[-continuation_pullback_lookback - 1 : -1]
        continuation_buffer = atr * continuation_buffer_atr
        continuation_high = float(
            retest_window["high"].tail(continuation_trigger_bars).max()
        )
        continuation_low = float(
            retest_window["low"].tail(continuation_trigger_bars).min()
        )
        long_pullback_seen = (
            not retest_window.empty
            and (
                retest_window["low"]
                <= (float(confirm_ema) + (atr * continuation_touch_atr))
            ).any()
        )
        short_pullback_seen = (
            not retest_window.empty
            and (
                retest_window["high"]
                >= (float(short_confirm_ema) - (atr * continuation_touch_atr))
            ).any()
        )

        long_confirmations = sum(
            close > (channel + breakout_buffer)
            for channel in upper_channels.values()
        )
        short_confirmations = sum(
            close < (channel - breakout_buffer)
            for channel in lower_channels.values()
        )

        long_trend_ok = (
            long_context_ready
            and not pd.isna(trend_ema_prev)
            and
            close > confirm_ema > trend_ema
            and trend_ema > trend_ema_prev
            and adx >= adx_min
            and volume_ratio >= volume_multiplier
            and momentum_pct >= min_momentum_pct
            and (di_plus is None or di_minus is None or di_plus > di_minus)
        )
        short_trend_ok = (
            short_context_ready
            and not pd.isna(short_trend_ema_prev)
            and
            close < short_confirm_ema < short_trend_ema
            and short_trend_ema < short_trend_ema_prev
            and adx >= short_adx_min
            and volume_ratio >= short_volume_multiplier
            and momentum_pct <= -short_min_momentum_pct
            and (di_plus is None or di_minus is None or di_minus > di_plus)
        )

        fresh_long_breakout = (
            prev_close <= (prev_upper_shortest + breakout_buffer)
            and close > (upper_channels[shortest_period] + breakout_buffer)
        )
        fresh_short_breakout = (
            prev_close >= (prev_lower_shortest - breakout_buffer)
            and close < (lower_channels[shortest_period] - breakout_buffer)
        )
        continuation_long = (
            allow_continuation_entries
            and long_pullback_seen
            and prev_close <= (continuation_high + continuation_buffer)
            and close > (continuation_high + continuation_buffer)
        )
        continuation_short = (
            allow_continuation_entries
            and short_pullback_seen
            and prev_close >= (continuation_low - continuation_buffer)
            and close < (continuation_low - continuation_buffer)
        )

        if (
            (long_confirmations >= min_confirmations or continuation_long)
            and (fresh_long_breakout or continuation_long)
            and long_trend_ok
        ):
            stop_anchor = min(stop_low, float(confirm_ema))
            stop_loss = stop_anchor - (atr * stop_buffer_atr)
            risk = close - stop_loss
            if risk <= 0:
                return None
            take_profit = close + (risk * take_profit_rr_ratio)
            confidence = min(
                0.95,
                0.62
                + (0.06 * long_confirmations)
                + min(0.12, max(0.0, adx - adx_min) / 100)
                + min(0.08, max(0.0, volume_ratio - volume_multiplier)),
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
                    f"Donchian {'continuation' if continuation_long and not fresh_long_breakout else 'breakout'} long "
                    f"| confirmations={long_confirmations}/{len(breakout_periods)} "
                    f"| ADX {adx:.1f} | Momentum {momentum_pct:.1f}%"
                ),
                metadata={
                    "confirmations": long_confirmations,
                    "breakout_periods": breakout_periods,
                    "entry_mode": (
                        "continuation"
                        if continuation_long and not fresh_long_breakout
                        else "breakout"
                    ),
                    "trend_ema": float(trend_ema),
                    "confirm_ema": float(confirm_ema),
                    "momentum_pct": momentum_pct,
                    "adx": adx,
                    "volume_ratio": volume_ratio,
                },
            )

        if (
            (short_confirmations >= min_confirmations or continuation_short)
            and (fresh_short_breakout or continuation_short)
            and short_trend_ok
        ):
            stop_anchor = max(stop_high, float(short_confirm_ema))
            stop_loss = stop_anchor + (atr * stop_buffer_atr)
            risk = stop_loss - close
            if risk <= 0:
                return None
            take_profit = close - (risk * take_profit_rr_ratio)
            confidence = min(
                0.95,
                0.62
                + (0.06 * short_confirmations)
                + min(0.12, max(0.0, adx - short_adx_min) / 100)
                + min(0.08, max(0.0, volume_ratio - short_volume_multiplier)),
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
                    f"Donchian {'continuation' if continuation_short and not fresh_short_breakout else 'breakout'} short "
                    f"| confirmations={short_confirmations}/{len(breakout_periods)} "
                    f"| ADX {adx:.1f} | Momentum {momentum_pct:.1f}%"
                ),
                metadata={
                    "confirmations": short_confirmations,
                    "breakout_periods": breakout_periods,
                    "entry_mode": (
                        "continuation"
                        if continuation_short and not fresh_short_breakout
                        else "breakout"
                    ),
                    "trend_ema": float(short_trend_ema),
                    "confirm_ema": float(short_confirm_ema),
                    "momentum_pct": momentum_pct,
                    "adx": adx,
                    "volume_ratio": volume_ratio,
                },
            )

        return None
