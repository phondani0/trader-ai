"""
Strategy: Range Reversion

Purpose:
    Express a disciplined mean-reversion sleeve for sideways crypto tapes.

Logic:
    - Only run when ADX is low and Bollinger bandwidth is compressed
    - Wait for a band overextension plus RSI extreme
    - Enter only after the next bar reclaims back inside the band
    - Target fair value reversion toward VWAP / Bollinger midline
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import MarketRegime, Signal, SignalType, Side, StrategyType
from src.strategies.base import BaseStrategy


class RangeReversionStrategy(BaseStrategy):
    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.MEAN_REVERSION

    @property
    def name(self) -> str:
        return "range_reversion"

    def indicator_requirements(self) -> dict[str, object]:
        bb_period = int(self.params.get("bb_period", 20))
        bb_std_dev = float(self.params.get("bb_std_dev", 2.0))
        rsi_period = int(self.params.get("rsi_period", 14))
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))
        return {
            "rsi": [rsi_period],
            "atr": [atr_period],
            "adx": [adx_period],
            "bbands": [(bb_period, bb_std_dev)],
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

        bb_period = int(self.params.get("bb_period", 20))
        bb_std_dev = float(self.params.get("bb_std_dev", 2.0))
        rsi_period = int(self.params.get("rsi_period", 14))
        atr_period = int(self.params.get("atr_period", 14))
        adx_period = int(self.params.get("adx_period", 14))

        bb_cols = self.bbands_cols(bb_period, bb_std_dev)
        rsi_col = self.rsi_col(rsi_period)
        atr_col = self.atr_col(atr_period)
        adx_cols = self.adx_cols(adx_period)

        required = [
            "close",
            "high",
            "low",
            "vwap",
            bb_cols["lower"],
            bb_cols["mid"],
            bb_cols["upper"],
            bb_cols["bandwidth"],
            rsi_col,
            atr_col,
            adx_cols["adx"],
        ]
        if not all(col in df.columns for col in required):
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(curr["close"])
        prev_close = float(prev["close"])
        low = float(curr["low"])
        high = float(curr["high"])
        prev_low = float(prev["low"])
        prev_high = float(prev["high"])
        vwap = curr["vwap"]
        bb_lower = curr[bb_cols["lower"]]
        bb_mid = curr[bb_cols["mid"]]
        bb_upper = curr[bb_cols["upper"]]
        bb_bandwidth = curr[bb_cols["bandwidth"]]
        rsi = curr[rsi_col]
        prev_rsi = prev[rsi_col]
        atr = curr[atr_col]
        adx = curr[adx_cols["adx"]]

        if any(
            pd.isna(value)
            for value in [
                vwap,
                bb_lower,
                bb_mid,
                bb_upper,
                bb_bandwidth,
                rsi,
                prev_rsi,
                atr,
                adx,
            ]
        ):
            return None
        if atr <= 0 or vwap == 0:
            return None

        adx_max = float(self.params.get("adx_max", 30))
        max_bandwidth = float(self.params.get("max_bandwidth", 1.2))
        rsi_oversold = float(self.params.get("rsi_oversold", 35))
        rsi_overbought = float(self.params.get("rsi_overbought", 65))
        touch_tolerance_pct = float(self.params.get("touch_tolerance_pct", 0.2)) / 100
        min_vwap_distance_pct = float(
            self.params.get("min_vwap_distance_pct", 0.5)
        )
        stop_buffer_atr = float(self.params.get("stop_buffer_atr", 0.6))
        take_profit_blend = float(self.params.get("take_profit_blend", 0.5))
        min_rr = float(self.params.get("min_rr", 2.0))

        if float(adx) > adx_max or float(bb_bandwidth) > max_bandwidth:
            return None

        vwap_distance_pct = abs(close - float(vwap)) / float(vwap) * 100
        if vwap_distance_pct < min_vwap_distance_pct:
            return None

        target = float(vwap) * take_profit_blend + float(bb_mid) * (1 - take_profit_blend)

        # Accept either prior-bar or current-bar band sweeps before reclaim.
        lower_touch = min(prev_low, low) <= float(bb_lower) * (1 + touch_tolerance_pct)
        upper_touch = max(prev_high, high) >= float(bb_upper) * (1 - touch_tolerance_pct)

        long_reclaim = close > float(bb_lower) and close > prev_close
        if (
            lower_touch
            and long_reclaim
            and float(prev_rsi) <= rsi_oversold
            and float(rsi) > float(prev_rsi)
            and close < target
        ):
            stop_loss = float(bb_lower) - (atr * stop_buffer_atr)
            take_profit = target
            if not self._check_risk_reward(close, stop_loss, take_profit, min_rr=min_rr):
                return None
            confidence = min(
                0.88,
                0.62
                + min(0.10, max(0.0, rsi_oversold - float(prev_rsi)) / 100)
                + min(0.08, vwap_distance_pct / 100)
                + min(0.08, max(0.0, adx_max - float(adx)) / 100),
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
                    f"Range reversion BUY | reclaim from lower band | "
                    f"RSI {float(prev_rsi):.1f}->{float(rsi):.1f}"
                ),
                metadata={
                    "vwap": float(vwap),
                    "bb_lower": float(bb_lower),
                    "bb_mid": float(bb_mid),
                    "vwap_distance_pct": vwap_distance_pct,
                    "adx": float(adx),
                },
            )

        short_reclaim = close < float(bb_upper) and close < prev_close
        if (
            upper_touch
            and short_reclaim
            and float(prev_rsi) >= rsi_overbought
            and float(rsi) < float(prev_rsi)
            and close > target
        ):
            stop_loss = float(bb_upper) + (atr * stop_buffer_atr)
            take_profit = target
            if not self._check_risk_reward(close, stop_loss, take_profit, min_rr=min_rr):
                return None
            confidence = min(
                0.88,
                0.62
                + min(0.10, max(0.0, float(prev_rsi) - rsi_overbought) / 100)
                + min(0.08, vwap_distance_pct / 100)
                + min(0.08, max(0.0, adx_max - float(adx)) / 100),
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
                    f"Range reversion SELL | reclaim from upper band | "
                    f"RSI {float(prev_rsi):.1f}->{float(rsi):.1f}"
                ),
                metadata={
                    "vwap": float(vwap),
                    "bb_upper": float(bb_upper),
                    "bb_mid": float(bb_mid),
                    "vwap_distance_pct": vwap_distance_pct,
                    "adx": float(adx),
                },
            )

        return None
