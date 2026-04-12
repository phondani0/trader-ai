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

    def indicator_requirements(self) -> dict[str, object]:
        return {
            "sma": [int(self.params.get("trend_sma", 50))],
            "rsi": [14],
            "atr": [14],
            "macd": [
                (
                    int(self.params.get("macd_fast", 12)),
                    int(self.params.get("macd_slow", 26)),
                    int(self.params.get("macd_signal", 9)),
                )
            ],
            "bbands": [
                (
                    int(self.params.get("bb_period", 20)),
                    float(self.params.get("bb_std_dev", 2.0)),
                )
            ],
        }

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        if len(df) < 30:
            return None

        trend_sma_period = int(self.params.get("trend_sma", 50))
        macd_fast = int(self.params.get("macd_fast", 12))
        macd_slow = int(self.params.get("macd_slow", 26))
        macd_signal = int(self.params.get("macd_signal", 9))
        bb_period = int(self.params.get("bb_period", 20))
        bb_std_dev = float(self.params.get("bb_std_dev", 2.0))

        bb_cols = self.bbands_cols(bb_period, bb_std_dev)
        macd_cols = self.macd_cols(macd_fast, macd_slow, macd_signal)
        trend_col = self.sma_col(trend_sma_period)
        rsi_col = self.rsi_col(14)
        atr_col = self.atr_col(14)

        required = [
            "close",
            bb_cols["lower"],
            bb_cols["upper"],
            bb_cols["mid"],
            macd_cols["macd"],
            macd_cols["signal"],
            macd_cols["hist"],
            atr_col,
            trend_col,
        ]
        if not all(col in df.columns for col in required):
            return None

        # Current and previous bar
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        close = curr["close"]
        bb_lower = curr[bb_cols["lower"]]
        bb_upper = curr[bb_cols["upper"]]
        bb_mid = curr[bb_cols["mid"]]
        trend_sma = curr[trend_col]
        macd_val = curr[macd_cols["macd"]]
        macd_sig = curr[macd_cols["signal"]]
        prev_macd = prev[macd_cols["macd"]]
        prev_sig = prev[macd_cols["signal"]]
        atr = curr[atr_col]

        if (
            pd.isna(bb_lower)
            or pd.isna(macd_val)
            or pd.isna(atr)
            or atr == 0
            or pd.isna(trend_sma)
        ):
            return None

        stop_atr_mult = self.params.get("stop_loss_atr_multiplier", 2.0)

        # ─── BUY Signal ───────────────────────────────────────────
        # Price at or below lower BB + MACD bullish crossover + Above SMA 50
        macd_bullish_cross = prev_macd <= prev_sig and macd_val > macd_sig
        price_at_lower_bb = close <= bb_lower * 1.002
        above_trend = close > trend_sma

        if price_at_lower_bb and macd_bullish_cross and above_trend:
            stop_loss = close - (atr * stop_atr_mult)
            # Exit at mid-band for higher win rate in mean reversion
            take_profit = bb_mid

            confidence = 0.65
            if rsi_col in df.columns and not pd.isna(curr.get(rsi_col)):
                if curr[rsi_col] < 35:
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
                metadata={"bb_lower": bb_lower, "trend_sma": trend_sma, "atr": atr},
            )

        # ─── SELL Signal ──────────────────────────────────────────
        # Price at or above upper BB + MACD bearish crossover + Below SMA 50
        macd_bearish_cross = prev_macd >= prev_sig and macd_val < macd_sig
        price_at_upper_bb = close >= bb_upper * 0.998
        below_trend = close < trend_sma

        if price_at_upper_bb and macd_bearish_cross and below_trend:
            stop_loss = close + (atr * stop_atr_mult)
            # Exit at mid-band
            take_profit = bb_mid

            confidence = 0.65
            if rsi_col in df.columns and not pd.isna(curr.get(rsi_col)):
                if curr[rsi_col] > 65:
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
                metadata={"bb_upper": bb_upper, "trend_sma": trend_sma, "atr": atr},
            )

        return None
