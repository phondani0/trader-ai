"""
Strategy 6: EMA Crossover (Trend Following)

Best in: Strong trending markets (ADX > 25-30)

Logic:
    BUY:  9 EMA crosses above 21 EMA + Price > VWAP + ADX > 25
    SELL: 9 EMA crosses below 21 EMA + Price < VWAP

Simple but effective when filtered by ADX for trend confirmation.
Uses trailing stop (2x ATR) to ride the trend.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import Signal, SignalType, Side, MarketRegime
from src.strategies.base import BaseStrategy


class EMACrossoverStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "ema_crossover"

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        if len(df) < 30:
            return None

        fast_period = self.params.get("fast_ema", 9)
        slow_period = self.params.get("slow_ema", 21)
        adx_min = self.params.get("adx_min", 25)
        require_vwap = self.params.get("require_vwap_alignment", True)
        trailing_atr = self.params.get("trailing_stop_atr", 2.0)

        fast_col = f"ema_{fast_period}"
        slow_col = f"ema_{slow_period}"

        required = [fast_col, slow_col, "close", "atr"]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        close = curr["close"]
        fast_ema = curr[fast_col]
        slow_ema = curr[slow_col]
        prev_fast = prev[fast_col]
        prev_slow = prev[slow_col]
        atr = curr["atr"]
        adx = curr.get("adx", 30)
        vwap = curr.get("vwap", close)

        if pd.isna(fast_ema) or pd.isna(slow_ema) or pd.isna(atr) or atr == 0:
            return None

        # ADX filter — only trade in strong trends
        if not pd.isna(adx) and adx < adx_min:
            return None

        # ─── BUY: Golden Cross (fast crosses above slow) ─────────
        bullish_cross = prev_fast <= prev_slow and fast_ema > slow_ema
        vwap_ok = close > vwap if (require_vwap and not pd.isna(vwap)) else True

        if bullish_cross and vwap_ok:
            stop_loss = close - (atr * trailing_atr)
            risk = close - stop_loss
            take_profit = close + (risk * 2.0)

            confidence = 0.6
            if not pd.isna(adx):
                if adx > 35:
                    confidence = 0.7
                if adx > 45:
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
                reason=f"EMA Golden Cross: {fast_period}EMA ({fast_ema:.2f}) > {slow_period}EMA ({slow_ema:.2f}) | ADX: {adx:.1f}",
                metadata={"fast_ema": fast_ema, "slow_ema": slow_ema, "adx": adx},
            )

        # ─── SELL: Death Cross (fast crosses below slow) ──────────
        bearish_cross = prev_fast >= prev_slow and fast_ema < slow_ema
        vwap_ok_sell = close < vwap if (require_vwap and not pd.isna(vwap)) else True

        if bearish_cross and vwap_ok_sell:
            stop_loss = close + (atr * trailing_atr)
            risk = stop_loss - close
            take_profit = close - (risk * 2.0)

            confidence = 0.6
            if not pd.isna(adx) and adx > 35:
                confidence = 0.7

            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                side=Side.SELL,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                regime=regime,
                reason=f"EMA Death Cross: {fast_period}EMA ({fast_ema:.2f}) < {slow_period}EMA ({slow_ema:.2f}) | ADX: {adx:.1f}",
                metadata={"fast_ema": fast_ema, "slow_ema": slow_ema, "adx": adx},
            )

        return None
