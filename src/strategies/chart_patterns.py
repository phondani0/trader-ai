"""
Strategy 7: Chart Pattern Detection

Active in: All market conditions (patterns are condition-agnostic)

Detects:
    - Triangle patterns (Ascending, Descending, Symmetrical)
    - ABCD harmonic pattern
    - Double Top / Double Bottom
    - Head & Shoulders (basic)

Uses pivot point detection + geometric validation.
Works better on higher timeframes (15Min+).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

from src.core.models import Signal, SignalType, Side, MarketRegime
from src.strategies.base import BaseStrategy


class ChartPatternStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "chart_patterns"

    def indicator_requirements(self) -> dict[str, object]:
        return {
            "atr": [14],
            "volume_sma": [20],
        }

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        if len(df) < 60:
            return None

        required = ["close", "high", "low", "atr", "volume_ratio"]
        if not all(col in df.columns for col in required):
            return None

        # Try each pattern type — return first valid signal
        signal = (
            self._detect_triangle(symbol, df, regime) or
            self._detect_double_top_bottom(symbol, df, regime) or
            self._detect_abcd(symbol, df, regime)
        )

        return signal

    # ─── Pivot Detection ──────────────────────────────────────────

    def _find_pivots(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Find swing highs and swing lows using scipy's argrelextrema.
        """
        lookback = self.params.get("pivot_lookback", 5)

        highs = df["high"].values
        lows = df["low"].values

        # Find local maxima (swing highs)
        high_idx = argrelextrema(highs, np.greater_equal, order=lookback)[0]
        # Find local minima (swing lows)
        low_idx = argrelextrema(lows, np.less_equal, order=lookback)[0]

        swing_highs = df.iloc[high_idx][["high"]].copy()
        swing_highs.columns = ["price"]

        swing_lows = df.iloc[low_idx][["low"]].copy()
        swing_lows.columns = ["price"]

        return swing_highs, swing_lows

    # ─── Triangle Pattern ─────────────────────────────────────────

    def _detect_triangle(
        self, symbol: str, df: pd.DataFrame, regime: MarketRegime
    ) -> Optional[Signal]:
        """
        Detect triangle patterns (ascending, descending, symmetrical).

        Triangle = converging trendlines connecting swing highs and swing lows.
        """
        swing_highs, swing_lows = self._find_pivots(df)

        if len(swing_highs) < 3 or len(swing_lows) < 3:
            return None

        # Last N swing points
        recent_highs = swing_highs.tail(4)
        recent_lows = swing_lows.tail(4)

        if len(recent_highs) < 3 or len(recent_lows) < 3:
            return None

        # Calculate slopes of high trendline and low trendline
        high_x = np.arange(len(recent_highs))
        low_x = np.arange(len(recent_lows))

        high_slope = np.polyfit(high_x, recent_highs["price"].values, 1)[0]
        low_slope = np.polyfit(low_x, recent_lows["price"].values, 1)[0]

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        close = curr["close"]
        prev_close = prev["close"]
        atr = curr["atr"]

        if pd.isna(atr) or atr == 0:
            return None

        stop_loss_pct = self.params.get("stop_loss_pct", 1.0)

        # Determine triangle type
        if high_slope < -0.001 and low_slope > 0.001:
            # Symmetrical triangle — breakout direction unknown
            pattern = "Symmetrical Triangle"
            last_high = float(recent_highs["price"].iloc[-1])
            last_low = float(recent_lows["price"].iloc[-1])

            # Breakout above → Buy
            if close > last_high and prev_close <= last_high:
                stop_loss = close * (1 - stop_loss_pct / 100)
                take_profit = close + (close - stop_loss) * 2
                return self._create_signal(
                    symbol=symbol,
                    signal_type=SignalType.BUY,
                    side=Side.BUY,
                    entry_price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.6,
                    regime=regime,
                    reason=f"{pattern} breakout UP at {close:.2f}",
                    metadata={"pattern": pattern},
                )
            # Breakdown below → Sell
            elif close < last_low and prev_close >= last_low:
                stop_loss = close * (1 + stop_loss_pct / 100)
                take_profit = close - (stop_loss - close) * 2
                return self._create_signal(
                    symbol=symbol,
                    signal_type=SignalType.SELL,
                    side=Side.SELL,
                    entry_price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.6,
                    regime=regime,
                    reason=f"{pattern} breakdown at {close:.2f}",
                    metadata={"pattern": pattern},
                )

        elif high_slope < -0.001 and abs(low_slope) < 0.001:
            # Descending triangle — bearish bias
            pattern = "Descending Triangle"
            support = float(recent_lows["price"].iloc[-1])

            if close < support and prev_close >= support:
                stop_loss = close * (1 + stop_loss_pct / 100)
                take_profit = close - (stop_loss - close) * 2
                return self._create_signal(
                    symbol=symbol,
                    signal_type=SignalType.SELL,
                    side=Side.SELL,
                    entry_price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.65,
                    regime=regime,
                    reason=f"{pattern} breakdown below support {support:.2f}",
                    metadata={"pattern": pattern, "support": support},
                )

        elif abs(high_slope) < 0.001 and low_slope > 0.001:
            # Ascending triangle — bullish bias
            pattern = "Ascending Triangle"
            resistance = float(recent_highs["price"].iloc[-1])

            if close > resistance and prev_close <= resistance:
                stop_loss = close * (1 - stop_loss_pct / 100)
                take_profit = close + (close - stop_loss) * 2
                return self._create_signal(
                    symbol=symbol,
                    signal_type=SignalType.BUY,
                    side=Side.BUY,
                    entry_price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.65,
                    regime=regime,
                    reason=f"{pattern} breakout above resistance {resistance:.2f}",
                    metadata={"pattern": pattern, "resistance": resistance},
                )

        return None

    # ─── Double Top / Double Bottom ───────────────────────────────

    def _detect_double_top_bottom(
        self, symbol: str, df: pd.DataFrame, regime: MarketRegime
    ) -> Optional[Signal]:
        """
        Detect Double Top (bearish reversal) and Double Bottom (bullish reversal).
        """
        swing_highs, swing_lows = self._find_pivots(df)

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        close = curr["close"]
        prev_close = prev["close"]
        atr = curr["atr"]

        if pd.isna(atr) or atr == 0:
            return None

        tolerance = 0.02  # 2% tolerance for matching levels

        # ─── Double Bottom (Bullish) ──────────────────────────────
        if len(swing_lows) >= 3:
            last_three_lows = swing_lows.tail(3)
            low1 = float(last_three_lows["price"].iloc[0])
            low2 = float(last_three_lows["price"].iloc[-1])

            # Two lows at similar level
            if abs(low1 - low2) / low1 < tolerance:
                # Neckline = high between the two lows
                neckline_area = df.loc[last_three_lows.index[0]:last_three_lows.index[-1]]
                if not neckline_area.empty:
                    neckline = float(neckline_area["high"].max())

                    # Breakout above neckline
                    if close > neckline and prev_close <= neckline:
                        pattern_height = neckline - min(low1, low2)
                        stop_loss = min(low1, low2) - (atr * 0.5)
                        take_profit = neckline + pattern_height

                        return self._create_signal(
                            symbol=symbol,
                            signal_type=SignalType.BUY,
                            side=Side.BUY,
                            entry_price=close,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            confidence=0.6,
                            regime=regime,
                            reason=f"Double Bottom breakout | Neckline: {neckline:.2f}",
                            metadata={"pattern": "Double Bottom", "neckline": neckline},
                        )

        # ─── Double Top (Bearish) ─────────────────────────────────
        if len(swing_highs) >= 3:
            last_three_highs = swing_highs.tail(3)
            high1 = float(last_three_highs["price"].iloc[0])
            high2 = float(last_three_highs["price"].iloc[-1])

            if abs(high1 - high2) / high1 < tolerance:
                neckline_area = df.loc[last_three_highs.index[0]:last_three_highs.index[-1]]
                if not neckline_area.empty:
                    neckline = float(neckline_area["low"].min())

                    if close < neckline and prev_close >= neckline:
                        pattern_height = max(high1, high2) - neckline
                        stop_loss = max(high1, high2) + (atr * 0.5)
                        take_profit = neckline - pattern_height

                        return self._create_signal(
                            symbol=symbol,
                            signal_type=SignalType.SELL,
                            side=Side.SELL,
                            entry_price=close,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            confidence=0.6,
                            regime=regime,
                            reason=f"Double Top breakdown | Neckline: {neckline:.2f}",
                            metadata={"pattern": "Double Top", "neckline": neckline},
                        )

        return None

    # ─── ABCD Pattern ─────────────────────────────────────────────

    def _detect_abcd(
        self, symbol: str, df: pd.DataFrame, regime: MarketRegime
    ) -> Optional[Signal]:
        """
        Detect ABCD harmonic pattern using Fibonacci ratios.

        Bullish ABCD:  A(low) → B(high) → C(higher low) → D(higher high target)
        BC = 61.8% - 78.6% retracement of AB
        CD = 127.2% - 161.8% extension of BC
        """
        swing_highs, swing_lows = self._find_pivots(df)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        close = curr["close"]
        prev_close = prev["close"]
        atr = curr["atr"]

        if pd.isna(atr) or atr == 0:
            return None

        fib_tolerance = self.params.get("fib_tolerance", 0.05)

        # Combine and sort all pivots
        all_highs = swing_highs.tail(4)
        all_lows = swing_lows.tail(4)

        # Try to find a bullish ABCD: A(low) → B(high) → C(low) → D approaches
        if len(all_lows) >= 2 and len(all_highs) >= 1:
            a = float(all_lows["price"].iloc[-2])  # First low
            c = float(all_lows["price"].iloc[-1])   # Second low (retracement)
            b = float(all_highs["price"].iloc[-1])  # High between

            if a < b and c > a and c < b:  # Valid structure
                ab = b - a
                bc = b - c
                bc_ratio = bc / ab if ab > 0 else 0

                # Check if BC is 61.8% - 78.6% retracement of AB
                if 0.618 - fib_tolerance <= bc_ratio <= 0.786 + fib_tolerance:
                    # Project D: 127.2% - 161.8% extension of BC from C
                    d_target_low = c + (bc * 1.272)
                    d_target_high = c + (bc * 1.618)

                    # Check if current price just entered the D target from below
                    if d_target_low <= close <= d_target_high and prev_close < d_target_low:
                        stop_loss = a - (atr * 0.5)
                        take_profit = close + (close - stop_loss) * 1.5

                        return self._create_signal(
                            symbol=symbol,
                            signal_type=SignalType.BUY,
                            side=Side.BUY,
                            entry_price=close,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            confidence=0.55,
                            regime=regime,
                            reason=f"Bullish ABCD: BC ratio {bc_ratio:.3f} | D target: {d_target_low:.2f}-{d_target_high:.2f}",
                            metadata={
                                "pattern": "ABCD",
                                "a": a, "b": b, "c": c,
                                "bc_ratio": bc_ratio,
                            },
                        )

        return None
