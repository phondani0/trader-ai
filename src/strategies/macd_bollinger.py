"""
Strategy 1: MACD + Bollinger Bands (Mean Reversion)

Best in: Sideways / Range-bound markets (ADX < 25)

Logic:
    BUY:  Price touches lower Bollinger Band + MACD crosses above signal line
    SELL: Price touches upper Bollinger Band + MACD crosses below signal line

The "Squeeze" variant: When BB width contracts (low vol), watch for breakout
with MACD histogram confirmation.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import Signal, SignalType, Side, MarketRegime
from src.strategies.base import BaseStrategy


class MACDBollingerStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "macd_bollinger"

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        if len(df) < 30:
            return None

        required = ["close", "bb_lower", "bb_upper", "bb_mid", "macd", "macd_signal", "macd_hist", "atr", "ema_200"]
        if not all(col in df.columns for col in required):
            return None

        # Current and previous bar
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        close = curr["close"]
        bb_lower = curr["bb_lower"]
        bb_upper = curr["bb_upper"]
        bb_mid = curr["bb_mid"]
        ema_50 = curr["sma_50"]  # SMA 50 or EMA 50
        macd_val = curr["macd"]
        macd_sig = curr["macd_signal"]
        prev_macd = prev["macd"]
        prev_sig = prev["macd_signal"]
        atr = curr["atr"]

        if pd.isna(bb_lower) or pd.isna(macd_val) or pd.isna(atr) or atr == 0 or pd.isna(ema_50):
            return None

        stop_atr_mult = self.params.get("stop_loss_atr_multiplier", 2.0)
        rr_ratio = self.params.get("take_profit_rr_ratio", 2.0)

        # ─── BUY Signal ───────────────────────────────────────────
        # Price at or below lower BB + MACD bullish crossover + Above SMA 50
        macd_bullish_cross = prev_macd <= prev_sig and macd_val > macd_sig
        price_at_lower_bb = close <= bb_lower * 1.002
        above_trend = close > ema_50

        if price_at_lower_bb and macd_bullish_cross and above_trend:
            stop_loss = close - (atr * stop_atr_mult)
            # Exit at mid-band for higher win rate in mean reversion
            take_profit = bb_mid 

            confidence = 0.65
            if "rsi_14" in df.columns and not pd.isna(curr.get("rsi_14")):
                if curr["rsi_14"] < 35:
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
                reason=f"Mean Reversion BUY | Price at lower BB ({bb_lower:.2f}) + Trend UP (Price > SMA50)",
                metadata={"bb_lower": bb_lower, "ema_50": ema_50, "atr": atr},
            )

        # ─── SELL Signal ──────────────────────────────────────────
        # Price at or above upper BB + MACD bearish crossover + Below SMA 50
        macd_bearish_cross = prev_macd >= prev_sig and macd_val < macd_sig
        price_at_upper_bb = close >= bb_upper * 0.998
        below_trend = close < ema_50

        if price_at_upper_bb and macd_bearish_cross and below_trend:
            stop_loss = close + (atr * stop_atr_mult)
            # Exit at mid-band
            take_profit = bb_mid

            confidence = 0.65
            if "rsi_14" in df.columns and not pd.isna(curr.get("rsi_14")):
                if curr["rsi_14"] > 65:
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
                reason=f"Mean Reversion SELL | Price at upper BB ({bb_upper:.2f}) + Trend DOWN (Price < SMA50)",
                metadata={"bb_upper": bb_upper, "ema_50": ema_50, "atr": atr},
            )

        return None
