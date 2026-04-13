"""
Strategy: MACD + Bollinger Breakout Continuation

Research-only inverse of the defunct mean-reversion MACD+Bollinger logic.

Instead of betting on reversion from the bands, this strategy assumes that
band expansion plus a same-direction MACD confirmation can signal trend
continuation after failed mean reversion.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import MarketRegime, Side, Signal, SignalType
from src.strategies.base import BaseStrategy


class MACDBollingerInverseStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "macd_bollinger_inverse"

    def indicator_requirements(self) -> dict[str, object]:
        return {
            "sma": [int(self.params.get("trend_sma", 50))],
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
        if len(df) < 40:
            return None

        trend_sma_period = int(self.params.get("trend_sma", 50))
        macd_fast = int(self.params.get("macd_fast", 12))
        macd_slow = int(self.params.get("macd_slow", 26))
        macd_signal = int(self.params.get("macd_signal", 9))
        bb_period = int(self.params.get("bb_period", 20))
        bb_std_dev = float(self.params.get("bb_std_dev", 2.0))
        width_lookback = int(self.params.get("bb_width_lookback", 20))

        bb_cols = self.bbands_cols(bb_period, bb_std_dev)
        macd_cols = self.macd_cols(macd_fast, macd_slow, macd_signal)
        trend_col = self.sma_col(trend_sma_period)
        atr_col = self.atr_col(14)

        required = [
            "close",
            "high",
            "low",
            bb_cols["lower"],
            bb_cols["upper"],
            bb_cols["mid"],
            macd_cols["macd"],
            macd_cols["signal"],
            trend_col,
            atr_col,
        ]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(curr["close"])
        prev_close = float(prev["close"])
        bb_lower = float(curr[bb_cols["lower"]])
        bb_upper = float(curr[bb_cols["upper"]])
        bb_mid = float(curr[bb_cols["mid"]])
        trend_sma = float(curr[trend_col])
        atr = float(curr[atr_col])
        macd_val = float(curr[macd_cols["macd"]])
        macd_sig = float(curr[macd_cols["signal"]])
        prev_macd = float(prev[macd_cols["macd"]])
        prev_sig = float(prev[macd_cols["signal"]])

        if atr <= 0:
            return None

        width_series = (
            (df[bb_cols["upper"]] - df[bb_cols["lower"]]) / df[bb_cols["mid"]]
        ).tail(width_lookback)
        bb_width = float(width_series.iloc[-1]) if not width_series.empty else 0.0
        bb_width_mean = float(width_series.mean()) if not width_series.empty else 0.0
        width_expanding = bb_width >= bb_width_mean * float(
            self.params.get("bb_width_expansion_factor", 1.0)
        )

        breakout_buffer = float(self.params.get("breakout_buffer_pct", 0.0)) / 100
        stop_atr_mult = float(self.params.get("stop_loss_atr_multiplier", 1.5))
        take_profit_rr = float(self.params.get("take_profit_rr_ratio", 2.5))

        macd_hist_rising = macd_val - macd_sig > prev_macd - prev_sig
        macd_hist_falling = macd_val - macd_sig < prev_macd - prev_sig
        macd_bullish = macd_val > macd_sig and macd_hist_rising
        macd_bearish = macd_val < macd_sig and macd_hist_falling

        bullish_breakout = (
            close >= bb_upper * (1 + breakout_buffer)
            and prev_close >= float(prev[bb_cols["upper"]]) * 0.995
        )
        bearish_breakout = (
            close <= bb_lower * (1 - breakout_buffer)
            and prev_close <= float(prev[bb_cols["lower"]]) * 1.005
        )
        above_trend = close > trend_sma
        below_trend = close < trend_sma

        if width_expanding and bullish_breakout and macd_bullish and above_trend:
            stop_anchor = max(bb_mid, trend_sma)
            stop_loss = stop_anchor - (atr * stop_atr_mult)
            risk = close - stop_loss
            if risk <= 0:
                return None
            take_profit = close + (risk * take_profit_rr)

            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                side=Side.BUY,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=0.7,
                regime=regime,
                reason="MACD+BB inverse long | upper-band breakout continuation",
                metadata={
                    "bb_upper": bb_upper,
                    "bb_width": bb_width,
                    "bb_width_mean": bb_width_mean,
                    "trend_sma": trend_sma,
                },
            )

        if width_expanding and bearish_breakout and macd_bearish and below_trend:
            stop_anchor = min(bb_mid, trend_sma)
            stop_loss = stop_anchor + (atr * stop_atr_mult)
            risk = stop_loss - close
            if risk <= 0:
                return None
            take_profit = close - (risk * take_profit_rr)

            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                side=Side.SELL,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=0.7,
                regime=regime,
                reason="MACD+BB inverse short | lower-band breakdown continuation",
                metadata={
                    "bb_lower": bb_lower,
                    "bb_width": bb_width,
                    "bb_width_mean": bb_width_mean,
                    "trend_sma": trend_sma,
                },
            )

        return None
