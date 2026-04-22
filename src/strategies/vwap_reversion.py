"""
Strategy 2: VWAP Mean Reversion

Best in: Sideways / Range-bound markets (ADX < 20)

Logic:
    BUY:  Price drops >2 std deviations below VWAP + RSI < 30 (oversold)
    SELL: Price rises >2 std deviations above VWAP + RSI > 70 (overbought)
    EXIT: Return to VWAP

VWAP acts as the "fair value" — institutional traders often revert price to VWAP.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import Signal, SignalType, Side, MarketRegime, StrategyType
from src.strategies.base import BaseStrategy


class VWAPReversionStrategy(BaseStrategy):

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.MEAN_REVERSION

    @property
    def name(self) -> str:
        return "vwap_reversion"

    def indicator_requirements(self) -> dict[str, object]:
        return {
            "rsi": [int(self.params.get("rsi_period", 14))],
            "atr": [14],
            "vwap": True,
        }

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        if len(df) < 30:
            return None

        rsi_period = int(self.params.get("rsi_period", 14))
        rsi_col = self.rsi_col(rsi_period)
        atr_col = self.atr_col(14)

        required = ["close", "vwap", rsi_col, atr_col]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        close = curr["close"]
        vwap = curr["vwap"]
        rsi = curr[rsi_col]
        atr = curr[atr_col]
        prev_close = prev["close"]
        prev_vwap = prev["vwap"]
        prev_rsi = prev[rsi_col]

        if (
            pd.isna(vwap)
            or pd.isna(rsi)
            or pd.isna(atr)
            or pd.isna(prev_vwap)
            or pd.isna(prev_rsi)
            or vwap == 0
            or atr == 0
        ):
            return None

        # Calculate VWAP deviation (how many std devs from VWAP)
        deviation_lookback = int(self.params.get("deviation_lookback", 80))
        recent_vwap_diff = df["close"].tail(deviation_lookback) - df["vwap"].tail(
            deviation_lookback
        )
        vwap_std = recent_vwap_diff.std()

        if pd.isna(vwap_std) or vwap_std == 0:
            return None

        deviation = (close - vwap) / vwap_std

        entry_threshold = self.params.get("vwap_std_entry", 2.0)
        rsi_oversold = self.params.get("rsi_oversold", 30)
        rsi_overbought = self.params.get("rsi_overbought", 70)
        stop_loss_pct = self.params.get("stop_loss_pct", 0.5)
        atr_stop_multiplier = float(self.params.get("atr_stop_multiplier", 1.2))
        min_vwap_distance_pct = float(self.params.get("min_vwap_distance_pct", 0.2))

        vwap_distance_pct = abs(close - vwap) / vwap * 100 if vwap else 0.0
        if vwap_distance_pct < min_vwap_distance_pct:
            return None

        # ─── BUY Signal ───────────────────────────────────────────
        # Price significantly below VWAP + RSI oversold + momentum is starting to mean revert
        bullish_reversal = close > prev_close and rsi >= prev_rsi and prev_close < prev_vwap
        if deviation < -entry_threshold and rsi < rsi_oversold and bullish_reversal:
            stop_loss = min(
                close * (1 - stop_loss_pct / 100),
                close - (atr * atr_stop_multiplier),
            )
            take_profit = vwap  # Target: return to VWAP

            # Ensure minimum risk:reward
            risk = close - stop_loss
            reward = take_profit - close
            if risk <= 0 or reward / risk < 1.0:
                return None

            confidence = min(0.8, 0.5 + abs(deviation - entry_threshold) * 0.1)

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
                    f"VWAP reversion BUY: {deviation:.1f}σ below VWAP ({vwap:.2f}) | "
                    f"RSI recovering {prev_rsi:.1f}->{rsi:.1f}"
                ),
                metadata={
                    "vwap": vwap,
                    "deviation": deviation,
                    "rsi": rsi,
                    "prev_rsi": prev_rsi,
                    "vwap_distance_pct": vwap_distance_pct,
                },
            )

        # ─── SELL Signal ──────────────────────────────────────────
        # Price significantly above VWAP + RSI overbought + momentum is starting to mean revert
        bearish_reversal = close < prev_close and rsi <= prev_rsi and prev_close > prev_vwap
        if deviation > entry_threshold and rsi > rsi_overbought and bearish_reversal:
            stop_loss = max(
                close * (1 + stop_loss_pct / 100),
                close + (atr * atr_stop_multiplier),
            )
            take_profit = vwap  # Target: return to VWAP

            risk = stop_loss - close
            reward = close - take_profit
            if risk <= 0 or reward / risk < 1.0:
                return None

            confidence = min(0.8, 0.5 + abs(deviation - entry_threshold) * 0.1)

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
                    f"VWAP reversion SELL: {deviation:.1f}σ above VWAP ({vwap:.2f}) | "
                    f"RSI cooling {prev_rsi:.1f}->{rsi:.1f}"
                ),
                metadata={
                    "vwap": vwap,
                    "deviation": deviation,
                    "rsi": rsi,
                    "prev_rsi": prev_rsi,
                    "vwap_distance_pct": vwap_distance_pct,
                },
            )

        return None
