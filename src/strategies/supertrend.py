"""
Strategy 3: Supertrend Momentum

Best in: Trending markets (ADX > 25)

Logic:
    BUY:  Supertrend flips from bearish to bullish (direction changes to 1)
          + Price above 20 EMA + Volume above average
    SELL: Supertrend flips from bullish to bearish (direction changes to -1)
          + Price below 20 EMA

Uses trailing stop based on the Supertrend value itself.
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

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        if len(df) < 30:
            return None

        required = ["close", "supertrend", "supertrend_dir", "ema_21", "ema_200", "volume_ratio", "atr"]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        close = curr["close"]
        st_val = curr["supertrend"]
        st_dir = curr["supertrend_dir"]
        prev_dir = prev["supertrend_dir"]
        ema_21 = curr["ema_21"]
        ema_200 = curr["ema_200"]
        vol_ratio = curr["volume_ratio"]
        atr = curr["atr"]

        if pd.isna(st_val) or pd.isna(st_dir) or pd.isna(prev_dir) or pd.isna(atr) or atr == 0 or pd.isna(ema_200):
            return None

        volume_threshold = self.params.get("volume_multiplier", 1.2)

        # ─── BUY Signal ───────────────────────────────────────────
        # Supertrend BULLISH + price above EMA 21 + Volume + Above EMA 200
        supertrend_flip_bullish = prev_dir < 0 and st_dir > 0
        price_above_ema21 = close > ema_21 if not pd.isna(ema_21) else True
        above_long_term_trend = close > ema_200
        volume_confirmed = vol_ratio > volume_threshold if not pd.isna(vol_ratio) else True

        if supertrend_flip_bullish and price_above_ema21 and above_long_term_trend and volume_confirmed:
            stop_loss = st_val
            risk = close - stop_loss
            if risk <= 0:
                return None
            take_profit = close + (risk * 2.0)  # Tighter R:R for higher win rate

            confidence = 0.7
            if "adx" in df.columns and not pd.isna(curr.get("adx")):
                if curr["adx"] > 30:
                    confidence = 0.8

            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                side=Side.BUY,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                regime=regime,
                reason=f"Supertrend Long | ST BULLISH + Above EMA200",
                metadata={"supertrend": st_val, "ema_200": ema_200, "adx": curr.get("adx")},
            )

        # ─── SELL Signal ──────────────────────────────────────────
        # Supertrend BEARISH + price below EMA 21 + Volume + Below EMA 200
        supertrend_flip_bearish = prev_dir > 0 and st_dir < 0
        price_below_ema21 = close < ema_21 if not pd.isna(ema_21) else True
        below_long_term_trend = close < ema_200

        if supertrend_flip_bearish and price_below_ema21 and below_long_term_trend and volume_confirmed:
            stop_loss = st_val
            risk = stop_loss - close
            if risk <= 0:
                return None
            take_profit = close - (risk * 2.0)

            confidence = 0.7
            if "adx" in df.columns and not pd.isna(curr.get("adx")):
                if curr["adx"] > 30:
                    confidence = 0.8

            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                side=Side.SELL,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                regime=regime,
                reason=f"Supertrend Short | ST BEARISH + Below EMA200",
                metadata={"supertrend": st_val, "ema_200": ema_200, "adx": curr.get("adx")},
            )

        return None
