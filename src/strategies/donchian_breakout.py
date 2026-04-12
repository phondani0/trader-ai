"""
Strategy: Donchian Breakout

Best in: Genuine trend and breakout expansions where price escapes a prior range
with momentum, volume support, and higher-timeframe alignment.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import Signal, SignalType, Side, MarketRegime
from src.strategies.base import BaseStrategy


class DonchianBreakoutStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "donchian_breakout"

    def indicator_requirements(self) -> dict[str, object]:
        return {
            "ema": [int(self.params.get("trend_ema_period", 200))],
            "atr": [int(self.params.get("atr_period", 14))],
            "adx": [int(self.params.get("adx_period", 14))],
            "volume_sma": [int(self.params.get("volume_period", 20))],
        }

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        channel_period = int(self.params.get("channel_period", 20))
        trend_ema_period = int(self.params.get("trend_ema_period", 200))
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))
        volume_period = int(self.params.get("volume_period", 20))

        min_bars = max(channel_period + 2, trend_ema_period + 2, 60)
        if len(df) < min_bars:
            return None

        trend_col = self.ema_col(trend_ema_period)
        atr_col = self.atr_col(atr_period)
        adx_cols = self.adx_cols(adx_period)
        volume_ratio_col = self.volume_ratio_col(volume_period)

        required = ["close", "high", "low", trend_col, atr_col, adx_cols["adx"], volume_ratio_col]
        if not all(col in df.columns for col in required):
            return None

        upper_channel = df["high"].shift(1).rolling(channel_period).max()
        lower_channel = df["low"].shift(1).rolling(channel_period).min()
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        curr_upper = upper_channel.iloc[-1]
        prev_upper = upper_channel.iloc[-2]
        curr_lower = lower_channel.iloc[-1]
        prev_lower = lower_channel.iloc[-2]

        close = float(curr["close"])
        prev_close = float(prev["close"])
        trend_ema = curr[trend_col]
        atr = curr[atr_col]
        adx = curr[adx_cols["adx"]]
        volume_ratio = curr[volume_ratio_col]

        if any(pd.isna(value) for value in [curr_upper, prev_upper, curr_lower, prev_lower, trend_ema, atr, adx]):
            return None
        if atr <= 0:
            return None

        adx_min = float(self.params.get("adx_min", 22))
        volume_multiplier = float(self.params.get("volume_multiplier", 1.1))
        breakout_buffer_atr = float(self.params.get("breakout_buffer_atr", 0.15))
        stop_loss_atr = float(self.params.get("stop_loss_atr", 2.0))
        take_profit_rr_ratio = float(self.params.get("take_profit_rr_ratio", 2.5))

        breakout_buffer = atr * breakout_buffer_atr
        above_trend = close > trend_ema
        below_trend = close < trend_ema
        volume_confirmed = pd.isna(volume_ratio) or volume_ratio >= volume_multiplier
        trend_confirmed = adx >= adx_min

        bullish_breakout = (
            close > (curr_upper + breakout_buffer)
            and prev_close <= prev_upper
            and above_trend
            and volume_confirmed
            and trend_confirmed
        )
        if bullish_breakout:
            stop_loss = close - (atr * stop_loss_atr)
            risk = close - stop_loss
            if risk <= 0:
                return None
            take_profit = close + (risk * take_profit_rr_ratio)
            confidence = min(
                0.9,
                0.6
                + max(0.0, (adx - adx_min) / 100)
                + max(0.0, (close - curr_upper) / max(atr, 1e-9)) * 0.05,
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
                    f"Donchian breakout BUY | Close {close:.2f} above {channel_period}-bar "
                    f"high {curr_upper:.2f} | ADX {adx:.1f}"
                ),
                metadata={
                    "channel_upper": float(curr_upper),
                    "trend_ema": float(trend_ema),
                    "adx": float(adx),
                    "volume_ratio": float(volume_ratio) if not pd.isna(volume_ratio) else None,
                },
            )

        bearish_breakout = (
            close < (curr_lower - breakout_buffer)
            and prev_close >= prev_lower
            and below_trend
            and volume_confirmed
            and trend_confirmed
        )
        if bearish_breakout:
            stop_loss = close + (atr * stop_loss_atr)
            risk = stop_loss - close
            if risk <= 0:
                return None
            take_profit = close - (risk * take_profit_rr_ratio)
            confidence = min(
                0.9,
                0.6
                + max(0.0, (adx - adx_min) / 100)
                + max(0.0, (curr_lower - close) / max(atr, 1e-9)) * 0.05,
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
                    f"Donchian breakout SELL | Close {close:.2f} below {channel_period}-bar "
                    f"low {curr_lower:.2f} | ADX {adx:.1f}"
                ),
                metadata={
                    "channel_lower": float(curr_lower),
                    "trend_ema": float(trend_ema),
                    "adx": float(adx),
                    "volume_ratio": float(volume_ratio) if not pd.isna(volume_ratio) else None,
                },
            )

        return None
