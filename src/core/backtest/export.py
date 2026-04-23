"""
Trade Export Utilities.

Provides functions to export raw trade logs to CSV for external auditing.
"""

import csv
from pathlib import Path
from typing import List

from src.core.backtest.models import BacktestTrade


def export_trades_to_csv(trades: List[BacktestTrade], filepath: str | Path):
    """
    Export a list of BacktestTrade objects to a CSV file.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    if not trades:
        return
        
    headers = [
        "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time",
        "pnl_pct", "regime", "strategy", "signal_id", "kill_switch_triggered"
    ]
    
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        for trade in trades:
            row = {
                "symbol": trade.symbol,
                "side": trade.side.value if hasattr(trade.side, "value") else str(trade.side),
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "entry_time": trade.entry_time.isoformat() if trade.entry_time else "",
                "exit_time": trade.exit_time.isoformat() if trade.exit_time else "",
                "pnl_pct": trade.pnl_pct,
                "regime": trade.regime,
                "strategy": trade.strategy,
                "signal_id": trade.signal_id,
                "kill_switch_triggered": trade.kill_switch_triggered,
            }
            writer.writerow(row)
