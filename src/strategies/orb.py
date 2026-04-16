"""
Strategy 4: Opening Range Breakout (ORB)

Best in: First 30 minutes of market (high volatility mornings)

Logic:
    1. Wait for the first 15-30 minutes to establish the "Opening Range" (OR)
    2. OR = High and Low of the first 15/30 minutes
    3. BUY:  Price breaks above OR High + Volume > 1.5x avg + Price > VWAP
    4. SELL: Price breaks below OR Low  + Price < VWAP
    5. Stop-loss at opposite side of the OR range

This is one of the most discussed profitable strategies on r/algotrading
and r/Daytrading for session-based markets. It remains disabled by default
in the crypto-first configuration.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional

import pandas as pd
import pytz

from src.core.models import Signal, SignalType, Side, MarketRegime, StrategyType
from src.strategies.base import BaseStrategy


ET = pytz.timezone("US/Eastern")


class ORBStrategy(BaseStrategy):

    def __init__(self, params: dict):
        super().__init__(params)
        self._orb_high: dict[str, float] = {}     # symbol -> ORB high
        self._orb_low: dict[str, float] = {}      # symbol -> ORB low
        self._orb_set: dict[str, bool] = {}        # symbol -> whether ORB is established
        self._traded_today: dict[str, bool] = {}   # symbol -> already traded today

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

    @property
    def name(self) -> str:
        return "orb"

    def indicator_requirements(self) -> dict[str, object]:
        requirements: dict[str, object] = {
            "atr": [14],
            "volume_sma": [20],
        }
        if self.params.get("require_vwap_alignment", True):
            requirements["vwap"] = True
        return requirements

    def reset_daily(self):
        """Reset ORB levels at start of each trading day."""
        self._orb_high.clear()
        self._orb_low.clear()
        self._orb_set.clear()
        self._traded_today.clear()

    def set_orb_range(self, symbol: str, df: pd.DataFrame, orb_minutes: int = 30):
        """
        Calculate the Opening Range from the first N minutes of trading.
        Call this at the end of the ORB window (e.g., 10:00 AM ET).
        """
        if df.empty:
            return

        # Filter to today's data only
        today = datetime.now(ET).date()

        # Get the first N minutes of bars
        market_open_time = datetime.combine(today, time(9, 30))
        orb_end_time = datetime.combine(today, time(9, 30 + orb_minutes // 60, orb_minutes % 60))

        # Handle timezone-naive index
        orb_bars = df[
            (df.index >= pd.Timestamp(market_open_time)) &
            (df.index <= pd.Timestamp(orb_end_time))
        ]

        if orb_bars.empty or len(orb_bars) < 2:
            return

        self._orb_high[symbol] = float(orb_bars["high"].max())
        self._orb_low[symbol] = float(orb_bars["low"].min())
        self._orb_set[symbol] = True

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        if len(df) < 10:
            return None

        # Check if ORB range is established
        if not self._orb_set.get(symbol, False):
            return None

        # Only trade once per symbol per day
        if self._traded_today.get(symbol, False):
            return None

        orb_high = self._orb_high[symbol]
        orb_low = self._orb_low[symbol]
        orb_range = orb_high - orb_low

        if orb_range <= 0:
            return None

        curr = df.iloc[-1]
        close = curr["close"]
        volume_ratio = curr.get("volume_ratio", 1.0)
        vwap = curr.get("vwap", close)
        atr = curr.get("atr", orb_range)

        if pd.isna(close) or pd.isna(vwap):
            return None

        # Check range is not too narrow or too wide
        range_pct = (orb_range / close) * 100
        min_range = self.params.get("min_range_pct", 0.3)
        max_range = self.params.get("max_range_pct", 2.0)

        if range_pct < min_range or range_pct > max_range:
            return None

        vol_threshold = self.params.get("volume_multiplier", 1.5)
        require_vwap = self.params.get("require_vwap_alignment", True)
        rr_ratio = self.params.get("take_profit_rr_ratio", 2.0)

        # ─── BUY Signal: Breakout above ORB High ──────────────────
        if close > orb_high:
            # Volume confirmation
            volume_ok = volume_ratio > vol_threshold if not pd.isna(volume_ratio) else True
            # VWAP alignment (price above VWAP for bullish confirmation)
            vwap_ok = close > vwap if (require_vwap and not pd.isna(vwap)) else True

            if volume_ok and vwap_ok:
                stop_loss = orb_low  # Stop at opposite side of range
                risk = close - stop_loss
                take_profit = close + (risk * rr_ratio)

                self._traded_today[symbol] = True

                return self._create_signal(
                    symbol=symbol,
                    signal_type=SignalType.STRONG_BUY,
                    side=Side.BUY,
                    entry_price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.7,
                    regime=regime,
                    reason=f"ORB BREAKOUT UP: Close {close:.2f} > ORB High {orb_high:.2f} | Range: {range_pct:.1f}% | Vol: {volume_ratio:.1f}x",
                    metadata={
                        "orb_high": orb_high, "orb_low": orb_low,
                        "orb_range_pct": range_pct, "volume_ratio": volume_ratio,
                    },
                )

        # ─── SELL Signal: Breakdown below ORB Low ─────────────────
        if close < orb_low:
            volume_ok = volume_ratio > vol_threshold if not pd.isna(volume_ratio) else True
            vwap_ok = close < vwap if (require_vwap and not pd.isna(vwap)) else True

            if volume_ok and vwap_ok:
                stop_loss = orb_high
                risk = stop_loss - close
                take_profit = close - (risk * rr_ratio)

                self._traded_today[symbol] = True

                return self._create_signal(
                    symbol=symbol,
                    signal_type=SignalType.STRONG_SELL,
                    side=Side.SELL,
                    entry_price=close,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.7,
                    regime=regime,
                    reason=f"ORB BREAKDOWN: Close {close:.2f} < ORB Low {orb_low:.2f} | Range: {range_pct:.1f}% | Vol: {volume_ratio:.1f}x",
                    metadata={
                        "orb_high": orb_high, "orb_low": orb_low,
                        "orb_range_pct": range_pct, "volume_ratio": volume_ratio,
                    },
                )

        return None
