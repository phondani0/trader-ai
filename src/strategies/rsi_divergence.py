"""
Strategy 5: RSI Divergence (Reversal Detection)

Best in: After extended trends (ADX declining from >30)

Logic:
    Bullish Divergence: Price makes lower low, but RSI makes higher low → Reversal UP
    Bearish Divergence: Price makes higher high, but RSI makes lower high → Reversal DOWN

Confirmed by MACD histogram reversal for extra confluence.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from src.core.models import Signal, SignalType, Side, MarketRegime
from src.strategies.base import BaseStrategy


class RSIDivergenceStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "rsi_divergence"

    def indicator_requirements(self) -> dict[str, object]:
        requirements: dict[str, object] = {
            "rsi": [int(self.params.get("rsi_period", 14))],
            "atr": [14],
        }
        if self.params.get("require_macd_confirmation", True):
            requirements["macd"] = [(12, 26, 9)]
        return requirements

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        if len(df) < 50:
            return None

        rsi_period = int(self.params.get("rsi_period", 14))
        rsi_col = self.rsi_col(rsi_period)
        atr_col = self.atr_col(14)
        macd_cols = self.macd_cols(12, 26, 9)

        required = ["close", "low", "high", rsi_col, atr_col]
        if not all(col in df.columns for col in required):
            return None

        lookback = self.params.get("lookback_bars", 30)
        min_swing_pct = self.params.get("min_swing_pct", 0.5)
        require_macd = self.params.get("require_macd_confirmation", True)
        rr_ratio = self.params.get("take_profit_rr_ratio", 2.0)

        window = df.tail(lookback)
        curr = df.iloc[-1]
        close = curr["close"]
        rsi = curr[rsi_col]
        atr = curr[atr_col]

        if pd.isna(rsi) or pd.isna(atr) or atr == 0:
            return None

        # Find swing lows and swing highs in the lookback window
        lows = window["low"]
        highs = window["high"]
        rsi_vals = window[rsi_col]

        # ─── Bullish Divergence ───────────────────────────────────
        # Price: lower low, RSI: higher low
        bullish_div = self._detect_bullish_divergence(lows, rsi_vals, min_swing_pct)

        if bullish_div:
            # MACD confirmation: histogram turning positive
            macd_confirmed = True
            if require_macd and macd_cols["hist"] in df.columns:
                hist = curr.get(macd_cols["hist"])
                prev_hist = df.iloc[-2].get(macd_cols["hist"])
                if not pd.isna(hist) and not pd.isna(prev_hist):
                    macd_confirmed = hist > prev_hist  # Histogram increasing

            if macd_confirmed:
                recent_low = float(lows.min())
                stop_loss = recent_low - (atr * 1.5)  # Increased from 0.5 to 1.5
                risk = close - stop_loss
                if risk <= 0:
                    return None
                take_profit = close + (risk * rr_ratio)

                return self._create_signal(
                    symbol=symbol,
                    signal_type=SignalType.BUY,
                    side=Side.BUY,
                    entry_price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.65,
                    regime=regime,
                    reason=f"Bullish RSI divergence | RSI: {rsi:.1f} | Price LL but RSI HL",
                    metadata={"rsi": rsi, "divergence": "bullish"},
                )

        # ─── Bearish Divergence ───────────────────────────────────
        # Price: higher high, RSI: lower high
        bearish_div = self._detect_bearish_divergence(highs, rsi_vals, min_swing_pct)

        if bearish_div:
            macd_confirmed = True
            if require_macd and macd_cols["hist"] in df.columns:
                hist = curr.get(macd_cols["hist"])
                prev_hist = df.iloc[-2].get(macd_cols["hist"])
                if not pd.isna(hist) and not pd.isna(prev_hist):
                    macd_confirmed = hist < prev_hist

            if macd_confirmed:
                recent_high = float(highs.max())
                stop_loss = recent_high + (atr * 0.5)
                risk = stop_loss - close
                if risk <= 0:
                    return None
                take_profit = close - (risk * rr_ratio)

                return self._create_signal(
                    symbol=symbol,
                    signal_type=SignalType.SELL,
                    side=Side.SELL,
                    entry_price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.65,
                    regime=regime,
                    reason=f"Bearish RSI divergence | RSI: {rsi:.1f} | Price HH but RSI LH",
                    metadata={"rsi": rsi, "divergence": "bearish"},
                )

        return None

    def _detect_bullish_divergence(
        self, lows: pd.Series, rsi: pd.Series, min_swing_pct: float
    ) -> bool:
        """
        Detect bullish divergence: price makes lower low, RSI makes higher low.
        """
        if len(lows) < 10:
            return False

        # Split into two halves and find the swing lows
        mid = len(lows) // 2
        first_half_low_idx = lows.iloc[:mid].idxmin()
        second_half_low_idx = lows.iloc[mid:].idxmin()

        first_price_low = lows[first_half_low_idx]
        second_price_low = lows[second_half_low_idx]

        first_rsi_low = rsi[first_half_low_idx]
        second_rsi_low = rsi[second_half_low_idx]

        if pd.isna(first_rsi_low) or pd.isna(second_rsi_low):
            return False

        # Price makes lower low
        price_lower_low = second_price_low < first_price_low
        swing_pct = abs(second_price_low - first_price_low) / first_price_low * 100
        significant_swing = swing_pct >= min_swing_pct

        # RSI makes higher low
        rsi_higher_low = second_rsi_low > first_rsi_low

        return price_lower_low and rsi_higher_low and significant_swing

    def _detect_bearish_divergence(
        self, highs: pd.Series, rsi: pd.Series, min_swing_pct: float
    ) -> bool:
        """
        Detect bearish divergence: price makes higher high, RSI makes lower high.
        """
        if len(highs) < 10:
            return False

        mid = len(highs) // 2
        first_half_high_idx = highs.iloc[:mid].idxmax()
        second_half_high_idx = highs.iloc[mid:].idxmax()

        first_price_high = highs[first_half_high_idx]
        second_price_high = highs[second_half_high_idx]

        first_rsi_high = rsi[first_half_high_idx]
        second_rsi_high = rsi[second_half_high_idx]

        if pd.isna(first_rsi_high) or pd.isna(second_rsi_high):
            return False

        price_higher_high = second_price_high > first_price_high
        swing_pct = abs(second_price_high - first_price_high) / first_price_high * 100
        significant_swing = swing_pct >= min_swing_pct

        rsi_lower_high = second_rsi_high < first_rsi_high

        return price_higher_high and rsi_lower_high and significant_swing
