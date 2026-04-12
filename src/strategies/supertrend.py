"""
Strategy 3: Supertrend Pullback Continuation

Best in: Trending and breakout windows with directional follow-through.

Logic:
    BUY:  Supertrend remains bullish
          + price structure is above the long-term trend
          + recent pullback tapped the trend EMA
          + current bar resumes higher through the previous high
    SELL: Symmetric logic for bearish trends

This avoids raw supertrend flips, which are too noisy on 5-minute crypto.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import Signal, SignalType, Side, MarketRegime
from src.strategies.base import BaseStrategy


class SupertrendStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "supertrend"

    def indicator_requirements(self) -> dict[str, object]:
        atr_period = int(self.params.get("atr_period", 14))
        atr_multiplier = float(self.params.get("atr_multiplier", 3.0))
        trend_ema_period = int(self.params.get("ema_period", 21))
        long_term_ema_period = int(self.params.get("long_term_ema_period", 200))
        volume_period = int(self.params.get("volume_period", 20))
        return {
            "ema": [trend_ema_period, long_term_ema_period],
            "atr": [atr_period],
            "adx": [14],
            "supertrend": [(atr_period, atr_multiplier)],
            "volume_sma": [volume_period],
        }

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        atr_period = int(self.params.get("atr_period", 14))
        atr_multiplier = float(self.params.get("atr_multiplier", 3.0))
        trend_ema_period = int(self.params.get("ema_period", 21))
        long_term_ema_period = int(self.params.get("long_term_ema_period", 200))
        slope_lookback = int(self.params.get("slope_lookback", 12))
        min_bars = max(long_term_ema_period + slope_lookback + 2, 60)
        if len(df) < min_bars:
            return None

        supertrend_cols = self.supertrend_cols(atr_period, atr_multiplier)
        trend_col = self.ema_col(trend_ema_period)
        long_term_col = self.ema_col(long_term_ema_period)
        volume_ratio_col = self.volume_ratio_col(
            int(self.params.get("volume_period", 20))
        )
        atr_col = self.atr_col(atr_period)

        required = [
            "close",
            "high",
            "low",
            supertrend_cols["value"],
            supertrend_cols["direction"],
            trend_col,
            long_term_col,
            volume_ratio_col,
            atr_col,
        ]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        slope_ref = df.iloc[-1 - slope_lookback]
        recent = df.tail(int(self.params.get("pullback_lookback", 8)) + 2)

        close = curr["close"]
        st_val = curr[supertrend_cols["value"]]
        st_dir = curr[supertrend_cols["direction"]]
        trend_ema = curr[trend_col]
        long_term_ema = curr[long_term_col]
        long_term_ema_prev = slope_ref[long_term_col]
        vol_ratio = curr[volume_ratio_col]
        atr = curr[atr_col]
        adx = curr.get("adx")
        di_plus = curr.get("di_plus")
        di_minus = curr.get("di_minus")

        if (
            pd.isna(st_val)
            or pd.isna(st_dir)
            or pd.isna(atr)
            or atr == 0
            or pd.isna(long_term_ema)
            or pd.isna(long_term_ema_prev)
        ):
            return None

        adx_min = float(self.params.get("adx_min", 22))
        if not pd.isna(adx) and adx < adx_min:
            return None

        volume_threshold = float(self.params.get("volume_multiplier", 1.0))
        if not pd.isna(vol_ratio) and vol_ratio < volume_threshold:
            return None

        long_trend_up = long_term_ema > long_term_ema_prev
        long_trend_down = long_term_ema < long_term_ema_prev
        pullback_atr = float(self.params.get("pullback_touch_atr", 0.6))
        stop_buffer_atr = float(self.params.get("stop_buffer_atr", 0.8))
        take_profit_rr = float(self.params.get("take_profit_rr_ratio", 2.2))

        recent_below_trend_ema = (
            recent["close"].iloc[:-1] <= recent[trend_col].iloc[:-1]
        ).fillna(False).any()
        recent_above_trend_ema = (
            recent["close"].iloc[:-1] >= recent[trend_col].iloc[:-1]
        ).fillna(False).any()
        recent_pullback_low = float(recent["low"].min())
        recent_pullback_high = float(recent["high"].max())

        bullish_resume = close > float(prev["high"]) and close > trend_ema
        bearish_resume = close < float(prev["low"]) and close < trend_ema
        touched_trend_ema_from_above = recent_pullback_low <= (trend_ema + (atr * pullback_atr))
        touched_trend_ema_from_below = recent_pullback_high >= (trend_ema - (atr * pullback_atr))

        if (
            st_dir > 0
            and close > long_term_ema
            and trend_ema > long_term_ema
            and long_trend_up
            and (pd.isna(di_plus) or pd.isna(di_minus) or di_plus > di_minus)
            and recent_below_trend_ema
            and touched_trend_ema_from_above
            and bullish_resume
        ):
            stop_anchor = min(recent_pullback_low, st_val, trend_ema)
            stop_loss = stop_anchor - (atr * stop_buffer_atr)
            risk = close - stop_loss
            if risk <= 0:
                return None
            take_profit = close + (risk * take_profit_rr)

            confidence = 0.65
            if not pd.isna(adx):
                if adx > 30:
                    confidence = 0.8
            if not pd.isna(vol_ratio) and vol_ratio > 1.4:
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
                    f"Supertrend Resume Long | ST bullish + EMA pullback reclaim "
                    f"| ADX: {adx:.1f}"
                ),
                metadata={
                    "supertrend": st_val,
                    "trend_ema": trend_ema,
                    "long_term_ema": long_term_ema,
                    "adx": adx,
                    "di_plus": di_plus,
                    "di_minus": di_minus,
                    "volume_ratio": vol_ratio,
                },
            )

        if (
            st_dir < 0
            and close < long_term_ema
            and trend_ema < long_term_ema
            and long_trend_down
            and (pd.isna(di_plus) or pd.isna(di_minus) or di_minus > di_plus)
            and recent_above_trend_ema
            and touched_trend_ema_from_below
            and bearish_resume
        ):
            stop_anchor = max(recent_pullback_high, st_val, trend_ema)
            stop_loss = stop_anchor + (atr * stop_buffer_atr)
            risk = stop_loss - close
            if risk <= 0:
                return None
            take_profit = close - (risk * take_profit_rr)

            confidence = 0.65
            if not pd.isna(adx):
                if adx > 30:
                    confidence = 0.8
            if not pd.isna(vol_ratio) and vol_ratio > 1.4:
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
                    f"Supertrend Resume Short | ST bearish + EMA pullback reject "
                    f"| ADX: {adx:.1f}"
                ),
                metadata={
                    "supertrend": st_val,
                    "trend_ema": trend_ema,
                    "long_term_ema": long_term_ema,
                    "adx": adx,
                    "di_plus": di_plus,
                    "di_minus": di_minus,
                    "volume_ratio": vol_ratio,
                },
            )

        return None
