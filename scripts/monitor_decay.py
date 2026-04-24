"""
Alpha Decay Monitor (L2 Feedback Loop)

Reads live trades from the database and compares their real-world 
risk-adjusted performance (Sharpe, Sortino, MDD) against the expected
benchmarks established during Walk-Forward Analysis.
"""

import sys
from pathlib import Path
import json
from datetime import datetime
from loguru import logger

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import DatabaseManager
from src.core.backtest.models import BacktestTrade
from src.core.backtest.engine import BacktestEngine
from src.core.models import Side

PROJECT_ROOT = Path(__file__).parent.parent

def load_approvals() -> dict:
    approvals_file = PROJECT_ROOT / "data" / "strategy_approvals.json"
    if not approvals_file.exists():
        logger.error(f"Approvals file not found at {approvals_file}")
        return {}
    with open(approvals_file, "r") as f:
        return json.load(f)

def load_live_trades(strategy: str, symbol: str, days: int = 30) -> list[BacktestTrade]:
    db = DatabaseManager()
    records = db.get_recent_trades(days=days)
    
    # Convert DB records to BacktestTrade for engine compatibility
    trades = []
    for r in records:
        if r["strategy_name"] != strategy or r["symbol"] != symbol:
            continue
            
        trades.append(BacktestTrade(
            symbol=r["symbol"],
            side=Side.BUY if r["side"].upper() == "BUY" else Side.SELL,
            entry_time=datetime.fromisoformat(r["entry_time"]),
            exit_time=datetime.fromisoformat(r["exit_time"]),
            entry_price=r["entry_price"],
            exit_price=r["exit_price"],
            pnl_pct=r["pnl_pct"],
            regime=r["regime"],
            strategy=r["strategy_name"],
            reason=r.get("reason_entry", "")
        ))
    return trades

def main():
    logger.info("Starting Alpha Decay Monitor...")
    approvals = load_approvals()
    if not approvals:
        return
        
    engine = BacktestEngine(initial_capital=10000.0)
    decay_alerts = []
    
    # Iterate through approved strategies
    strategies_data = approvals.get("strategies", {})
    for strategy_name, data in strategies_data.items():
        if not data.get("approved_regimes"):
            continue
            
        logger.info(f"Analyzing Live Performance for: {strategy_name}")
        
        # In a multi-symbol setup, we'd iterate symbols. Let's assume BTC for now
        # or find symbols from the DB.
        symbol = "BTC/USDT:USDT"
        live_trades = load_live_trades(strategy_name, symbol, days=30)
        
        if len(live_trades) < 5:
            logger.info(f"  └─ Insufficient live trades ({len(live_trades)}) for statistical significance.")
            continue
            
        # Calculate live metrics using BacktestEngine's summarize logic
        period_start = min(t.exit_time for t in live_trades)
        period_end = max(t.exit_time for t in live_trades)
        live_result = engine.summarize(symbol, period_start, period_end, live_trades)
        
        # Compare against benchmark expectations
        # For simplicity, we use the average expected metrics across approved regimes
        regime_metrics = []
        for reg_name, profile in data.get("market_profiles", {}).items():
            if profile.get("approved"):
                regime_metrics.append(profile.get("metrics", {}))
                
        if not regime_metrics:
            continue
            
        # Average the expected Sortino
        avg_expected_sortino = sum(m.get("sortino_ratio", 0) for m in regime_metrics) / len(regime_metrics)
        expected_95_mdd = sum(m.get("mc_worst_95_mdd_pct", 0) for m in regime_metrics) / len(regime_metrics)
        
        live_sortino = live_result.sortino_ratio
        live_mdd = live_result.max_drawdown_pct
        
        logger.info(f"  └─ Live Sortino: {live_sortino:.2f} | Expected: {avg_expected_sortino:.2f}")
        logger.info(f"  └─ Live Max DD: {live_mdd:.2f}% | 95% MC Bound: {expected_95_mdd:.2f}%")
        
        # Decay thresholds
        if live_sortino < (avg_expected_sortino * 0.5):
            alert = f"🚨 DECAY ALERT: {strategy_name} Sortino dropped >50% below benchmark (Live: {live_sortino:.2f})"
            decay_alerts.append(alert)
            logger.warning(alert)
            
        if live_mdd < expected_95_mdd:
            alert = f"🚨 DECAY ALERT: {strategy_name} Drawdown exceeded 95% Monte Carlo bound! (Live: {live_mdd:.2f}%)"
            decay_alerts.append(alert)
            logger.error(alert)
            
    logger.info("\n" + "═" * 60)
    if decay_alerts:
        logger.error(f"Found {len(decay_alerts)} Active Decay Alerts. Strategies may need retraining.")
    else:
        logger.success("No significant alpha decay detected. All strategies within normal bounds.")
    logger.info("═" * 60)

if __name__ == "__main__":
    main()
