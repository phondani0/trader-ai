"""
Strategy 6: EMA Trend Resume

Best in: Persistent trend and breakout windows.

Logic:
    BUY:  Fast EMA > Slow EMA > Long EMA
          + long EMA slope positive
          + recent pullback into the EMA stack
          + breakout through the previous candle high
    SELL: Symmetric logic for bearish trends

This is intentionally stricter than a raw crossover. On 5-minute crypto,
crosses alone are too late and too noisy.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import Signal, SignalType, Side, MarketRegime, StrategyType
from src.strategies.base import BaseStrategy


class EMACrossoverStrategy(BaseStrategy):
    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

    @property
    def name(self) -> str:
        return "ema_crossover"

    def indicator_requirements(self) -> dict[str, object]:
        fast_period = int(self.params.get("fast_ema", 20))
        slow_period = int(self.params.get("slow_ema", 50))
        long_term_period = int(self.params.get("long_term_ema", 200))
        volume_period = int(self.params.get("volume_period", 20))

        requirements: dict[str, object] = {
            "ema": [fast_period, slow_period, long_term_period],
            "atr": [14],
            "adx": [14],
            "volume_sma": [volume_period],
        }
        if self.params.get("require_vwap_alignment", True):
            requirements["vwap"] = True
        return requirements

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        fast_period = int(self.params.get("fast_ema", 20))
        slow_period = int(self.params.get("slow_ema", 50))
        long_term_period = int(self.params.get("long_term_ema", 200))
        slope_lookback = int(self.params.get("slope_lookback", 12))

        min_bars = max(long_term_period + slope_lookback + 2, 60)
        if len(df) < min_bars:
            return None

        adx_min = float(self.params.get("adx_min", 22))
        require_vwap = bool(self.params.get("require_vwap_alignment", False))
        pullback_lookback = int(self.params.get("pullback_lookback", 8))
        volume_multiplier = float(self.params.get("volume_multiplier", 1.0))
        ema_touch_atr = float(self.params.get("ema_touch_atr", 0.6))
        stop_buffer_atr = float(self.params.get("stop_buffer_atr", 0.8))
        take_profit_rr = float(self.params.get("take_profit_rr_ratio", 2.4))

        fast_col = self.ema_col(fast_period)
        slow_col = self.ema_col(slow_period)
        long_col = self.ema_col(long_term_period)
        atr_col = self.atr_col(14)
        volume_ratio_col = self.volume_ratio_col(
            int(self.params.get("volume_period", 20))
        )

        required = [fast_col, slow_col, long_col, "close", "high", "low", atr_col, volume_ratio_col]
        if require_vwap:
            required.append("vwap")
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        slope_ref = df.iloc[-1 - slope_lookback]
        recent = df.tail(pullback_lookback + 2)

        close = curr["close"]
        fast_ema = curr[fast_col]
        slow_ema = curr[slow_col]
        long_ema = curr[long_col]
        long_ema_prev = slope_ref[long_col]
        atr = curr[atr_col]
        adx = curr.get("adx")
        di_plus = curr.get("di_plus")
        di_minus = curr.get("di_minus")
        vwap = curr.get("vwap", close)
        volume_ratio = curr.get(volume_ratio_col)

        if (
            pd.isna(fast_ema)
            or pd.isna(slow_ema)
            or pd.isna(long_ema)
            or pd.isna(long_ema_prev)
            or pd.isna(atr)
            or atr == 0
        ):
            return None

        if not pd.isna(adx) and adx < adx_min:
            return None

        if not pd.isna(volume_ratio) and volume_ratio < volume_multiplier:
            return None

        long_slope_up = long_ema > long_ema_prev
        long_slope_down = long_ema < long_ema_prev
        bullish_stack = fast_ema > slow_ema > long_ema
        bearish_stack = fast_ema < slow_ema < long_ema

        recent_below_fast = (
            recent["close"].iloc[:-1] <= recent[fast_col].iloc[:-1]
        ).fillna(False).any()
        recent_above_fast = (
            recent["close"].iloc[:-1] >= recent[fast_col].iloc[:-1]
        ).fillna(False).any()

        recent_pullback_low = float(recent["low"].min())
        recent_pullback_high = float(recent["high"].max())
        touched_slow_from_above = recent_pullback_low <= (slow_ema + (atr * ema_touch_atr))
        touched_slow_from_below = recent_pullback_high >= (slow_ema - (atr * ema_touch_atr))

        breakout_long = close > float(prev["high"]) and close > fast_ema
        breakout_short = close < float(prev["low"]) and close < fast_ema

        vwap_ok = close > vwap if (require_vwap and not pd.isna(vwap)) else True
        vwap_ok_sell = close < vwap if (require_vwap and not pd.isna(vwap)) else True

        if (
            bullish_stack
            and long_slope_up
            and (pd.isna(di_plus) or pd.isna(di_minus) or di_plus > di_minus)
            and recent_below_fast
            and touched_slow_from_above
            and breakout_long
            and vwap_ok
        ):
            stop_anchor = min(recent_pullback_low, slow_ema)
            stop_loss = stop_anchor - (atr * stop_buffer_atr)
            risk = close - stop_loss
            if risk <= 0:
                return None
            take_profit = close + (risk * take_profit_rr)

            confidence = 0.65
            if not pd.isna(adx):
                if adx > 35:
                    confidence = 0.8
            if not pd.isna(volume_ratio) and volume_ratio > 1.4:
                confidence = min(0.9, confidence + 0.05)

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
                    f"EMA Trend Resume Long | EMA stack bullish + pullback reclaim "
                    f"| ADX: {adx:.1f}"
                ),
                metadata={
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "long_ema": long_ema,
                    "adx": adx,
                    "di_plus": di_plus,
                    "di_minus": di_minus,
                    "volume_ratio": volume_ratio,
                },
            )

        if (
            bearish_stack
            and long_slope_down
            and (pd.isna(di_plus) or pd.isna(di_minus) or di_minus > di_plus)
            and recent_above_fast
            and touched_slow_from_below
            and breakout_short
            and vwap_ok_sell
        ):
            stop_anchor = max(recent_pullback_high, slow_ema)
            stop_loss = stop_anchor + (atr * stop_buffer_atr)
            risk = stop_loss - close
            if risk <= 0:
                return None
            take_profit = close - (risk * take_profit_rr)

            confidence = 0.65
            if not pd.isna(adx) and adx > 35:
                confidence = 0.8
            if not pd.isna(volume_ratio) and volume_ratio > 1.4:
                confidence = min(0.9, confidence + 0.05)

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
                    f"EMA Trend Resume Short | EMA stack bearish + pullback reject "
                    f"| ADX: {adx:.1f}"
                ),
                metadata={
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "long_ema": long_ema,
                    "adx": adx,
                    "di_plus": di_plus,
                    "di_minus": di_minus,
                    "volume_ratio": volume_ratio,
                },
            )

        return None
