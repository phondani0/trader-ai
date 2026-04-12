"""
Backtest Models.
Defines structured results for strategies and benchmarks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class BacktestTrade:
    """Record of a single trade in a backtest."""

    symbol: str
    side: str
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    pnl_pct: float
    regime: str
    strategy: str
    reason: str


@dataclass
class BacktestResult:
    """Consolidated results of a strategy backtest."""

    symbol: str
    period_start: datetime
    period_end: datetime
    trades: List[BacktestTrade] = field(default_factory=list)
    initial_capital: float = 10000.0
    final_equity: float = 0.0
    net_roi_pct: float = 0.0
    gross_roi_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expectancy_pct: float = 0.0
    profit_factor: float = 0.0
    total_fees_paid: float = 0.0
    killed_by_drawdown: bool = False
    kill_switch_reason: str = ""
    regime_breakdown: Dict = field(default_factory=dict)
    strategy_breakdown: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Helper for JSON/CSV export."""
        return {
            "symbol": self.symbol,
            "period": f"{self.period_start} to {self.period_end}",
            "net_roi": self.net_roi_pct,
            "win_rate": self.win_rate_pct,
            "mdd": self.max_drawdown_pct,
            "expectancy_pct": self.expectancy_pct,
            "profit_factor": self.profit_factor,
            "killed_by_drawdown": self.killed_by_drawdown,
            "trades": len(self.trades),
        }
