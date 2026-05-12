"""
Core data models — sidecar-only.

Kept types are used exclusively by the scanner sidecar stack:
  - MarketRegime, MarketBias, RegimeState  → regime_detector.py
  - Trade                                  → database.py
  - TradingMode                            → risk/manager.py, CCXTBroker

All other models (Order, Position, Signal, Side, OrderType, etc.) are
superseded by NautilusTrader's native typed models in the TradingNode.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TradingMode(str, enum.Enum):
    PAPER = "paper"
    LIVE = "live"


class MarketRegime(str, enum.Enum):
    TRENDING_LOW_VOL = "trending_low_vol"
    TRENDING_HIGH_VOL = "trending_high_vol"
    SIDEWAYS_LOW_VOL = "sideways_low_vol"
    SIDEWAYS_HIGH_VOL = "sideways_high_vol"
    UNKNOWN = "unknown"


class MarketBias(str, enum.Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class RegimeState(BaseModel):
    """Combined context of market regime and macro bias."""
    regime: MarketRegime
    bias: MarketBias
    is_burst: bool = False
    is_sweep: bool = False


class Trade(BaseModel):
    """Completed trade (entry + exit) — persisted to SQLite by DatabaseManager."""
    id: Optional[int] = None
    symbol: str
    side: str  # "buy" | "sell"
    quantity: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    strategy_name: str
    pnl: float = 0.0
    pnl_pct: float = 0.0
    regime: MarketRegime = MarketRegime.UNKNOWN
    reason_entry: str = ""
    reason_exit: str = ""

    def calculate_pnl(self):
        """Calculate realized P&L."""
        if self.side == "buy":
            self.pnl = (self.exit_price - self.entry_price) * self.quantity
        else:
            self.pnl = (self.entry_price - self.exit_price) * self.quantity
        if self.entry_price > 0 and self.quantity > 0:
            self.pnl_pct = (self.pnl / (self.entry_price * self.quantity)) * 100
        else:
            self.pnl_pct = 0.0
