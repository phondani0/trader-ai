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

        required = ["close", "supertrend", "supertrend_dir", "ema_21", "volume_ratio", "atr"]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        close = curr["close"]
        st_val = curr["supertrend"]
        st_dir = curr["supertrend_dir"]
        prev_dir = prev["supertrend_dir"]
        ema_21 = curr["ema_21"]
        vol_ratio = curr["volume_ratio"]
        atr = curr["atr"]

        if pd.isna(st_val) or pd.isna(st_dir) or pd.isna(prev_dir) or pd.isna(atr) or atr == 0:
            return None

        volume_threshold = self.params.get("volume_multiplier", 1.2)

        # ─── BUY Signal ───────────────────────────────────────────
        # Supertrend flips to bullish + price above EMA + volume confirmation
        supertrend_flip_bullish = prev_dir < 0 and st_dir > 0
        price_above_ema = close > ema_21 if not pd.isna(ema_21) else True
        volume_confirmed = vol_ratio > volume_threshold if not pd.isna(vol_ratio) else True

        if supertrend_flip_bullish and price_above_ema and volume_confirmed:
            stop_loss = st_val  # Supertrend value acts as stop
            risk = close - stop_loss
            if risk <= 0:
                return None
            take_profit = close + (risk * 2.5)  # 1:2.5 R:R for momentum trades

            confidence = 0.65
            # Boost if ADX is strong
            if "adx" in df.columns and not pd.isna(curr.get("adx")):
                if curr["adx"] > 30:
                    confidence = 0.75
                if curr["adx"] > 40:
                    confidence = 0.85

            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                side=Side.BUY,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                regime=regime,
                reason=f"Supertrend BULLISH flip | ST: {st_val:.2f} | EMA21: {ema_21:.2f} | Vol: {vol_ratio:.1f}x",
                metadata={"supertrend": st_val, "adx": curr.get("adx"), "vol_ratio": vol_ratio},
            )

        # ─── SELL Signal ──────────────────────────────────────────
        supertrend_flip_bearish = prev_dir > 0 and st_dir < 0
        price_below_ema = close < ema_21 if not pd.isna(ema_21) else True

        if supertrend_flip_bearish and price_below_ema and volume_confirmed:
            stop_loss = st_val
            risk = stop_loss - close
            if risk <= 0:
                return None
            take_profit = close - (risk * 2.5)

            confidence = 0.65
            if "adx" in df.columns and not pd.isna(curr.get("adx")):
                if curr["adx"] > 30:
                    confidence = 0.75

            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                side=Side.SELL,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                regime=regime,
                reason=f"Supertrend BEARISH flip | ST: {st_val:.2f} | EMA21: {ema_21:.2f} | Vol: {vol_ratio:.1f}x",
                metadata={"supertrend": st_val, "adx": curr.get("adx"), "vol_ratio": vol_ratio},
            )

        return None
