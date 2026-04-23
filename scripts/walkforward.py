"""
Walk-Forward Analysis Runner.

Executes Walk-Forward Analysis (WFA) on a given strategy to identify
optimal parameters over time and measure out-of-sample degradation.

Usage:
    python scripts/walkforward.py --symbol BTC/USDT:USDT --strategy supertrend --days 365
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from loguru import logger

from src.core.backtest.engine import BacktestEngine
from src.core.backtest.walkforward import WalkForwardOptimizer
from src.core.broker_factory import BrokerFactory
from src.core.data_feed import DataFeed
from src.core.models import TradingMode

# Import strategy classes directly for dynamic instantiation
from src.strategies.bearish_breakdown_continuation import BearishBreakdownContinuationStrategy
from src.strategies.chart_patterns import ChartPatternStrategy
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.ema_crossover import EMACrossoverStrategy
from src.strategies.macd_bollinger import MACDBollingerStrategy
from src.strategies.macd_bollinger_inverse import MACDBollingerInverseStrategy
from src.strategies.orb import ORBStrategy
from src.strategies.range_reversion import RangeReversionStrategy
from src.strategies.rsi_divergence import RSIDivergenceStrategy
from src.strategies.supertrend import SupertrendStrategy
from src.strategies.volume_weighted_tsmom import VolumeWeightedTSMomentumStrategy
from src.strategies.vwap_reversion import VWAPReversionStrategy
from src.strategies.episodic_pivot import EpisodicPivotStrategy
from src.strategies.episodic_pivot_short import EpisodicPivotShortStrategy

PROJECT_ROOT = Path(__file__).parent.parent

STRATEGY_CLASSES = {
    "macd_bollinger": MACDBollingerStrategy,
    "macd_bollinger_inverse": MACDBollingerInverseStrategy,
    "vwap_reversion": VWAPReversionStrategy,
    "range_reversion": RangeReversionStrategy,
    "supertrend": SupertrendStrategy,
    "orb": ORBStrategy,
    "rsi_divergence": RSIDivergenceStrategy,
    "ema_crossover": EMACrossoverStrategy,
    "donchian_breakout": DonchianBreakoutStrategy,
    "bearish_breakdown_continuation": BearishBreakdownContinuationStrategy,
    "volume_weighted_tsmom": VolumeWeightedTSMomentumStrategy,
    "chart_patterns": ChartPatternStrategy,
    "episodic_pivot": EpisodicPivotStrategy,
    "episodic_pivot_short": EpisodicPivotShortStrategy,
}


def load_settings() -> dict:
    with open(PROJECT_ROOT / "config" / "settings.yaml", "r") as f:
        return yaml.safe_load(f)


def load_strategy_config(strategy_name: str) -> dict:
    with open(PROJECT_ROOT / "config" / "strategies.yaml", "r") as f:
        strat_config = yaml.safe_load(f)
    return strat_config.get(strategy_name, {})


def print_wfa_results(wfa_results: dict):
    """Format and print the WFA results."""
    if not wfa_results:
        logger.warning("No WFA results to display.")
        return

    logger.info(f"\n{'═' * 80}")
    logger.info("WALK-FORWARD ANALYSIS OOS PERFORMANCE")
    logger.info(f"{'═' * 80}")
    
    total_oos = wfa_results["total_oos_result"]
    logger.info(f"Total OOS Splits: {wfa_results['splits_count']}")
    logger.info(f"Period: {total_oos.period_start} to {total_oos.period_end}")
    logger.info(f"Total Trades: {len(total_oos.trades)}")
    logger.info(f"Win Rate: {total_oos.win_rate_pct:.1f}%")
    logger.info(f"{'-' * 80}")
    logger.info(f"NET Return (compounded): {total_oos.net_roi_pct:.2f}%")
    logger.info(f"Max Drawdown: {total_oos.max_drawdown_pct:.2f}%")
    logger.info(f"Final Equity: ${total_oos.final_equity:,.2f}")
    logger.info(f"{'-' * 80}")
    
    sharpe_str = f"{total_oos.sharpe_ratio:.3f}" if total_oos.sharpe_ratio != float("inf") else "inf"
    sortino_str = f"{total_oos.sortino_ratio:.3f}" if total_oos.sortino_ratio != float("inf") else "inf"
    calmar_str = f"{total_oos.calmar_ratio:.3f}" if total_oos.calmar_ratio != float("inf") else "inf"
    
    logger.info(f"Sharpe Ratio:  {sharpe_str}  (target: >1.5)")
    logger.info(f"Sortino Ratio: {sortino_str}  (target: >2.0)")
    logger.info(f"Calmar Ratio:  {calmar_str}  (target: >1.0)")
    logger.info(f"{'═' * 80}")
    
    logger.info("\nParameter Evolution & Degradation:")
    avg_deg = sum(h["degradation_pct"] for h in wfa_results["param_history"]) / len(wfa_results["param_history"])
    
    for hist in wfa_results["param_history"]:
        deg_str = f"{hist['degradation_pct']:>6.1f}%"
        if hist['degradation_pct'] > 30:
            deg_str += " ⚠️"
        logger.info(
            f"Split {hist['split_idx']+1:02d} | Test: {hist['test_start'].strftime('%Y-%m-%d')} | "
            f"IS: {hist['is_metric']:>5.2f} -> OOS: {hist['oos_metric']:>5.2f} | "
            f"Degradation: {deg_str}"
        )
        # Log the params that were actually grid searched
        logger.info(f"         └─ Params: {hist['best_params']}")

    logger.info(f"{'-' * 80}")
    logger.info(f"Average OOS Degradation: {avg_deg:.1f}% (target: < 30%)")
    if avg_deg > 50:
        logger.error("❌ High degradation indicates severe curve-fitting or regime non-stationarity.")
    elif avg_deg < 0:
        logger.success("✅ Negative degradation (OOS beat IS) indicates highly robust parameters.")
    elif avg_deg <= 30:
        logger.success("✅ Acceptable degradation. Strategy demonstrates robustness.")
    else:
        logger.warning("⚠️ Moderate degradation. Monitor closely in live trading.")


def main():
    parser = argparse.ArgumentParser(description="Run Walk-Forward Analysis")
    parser.add_argument("--symbol", default="BTC/USDT:USDT", help="Symbol to test")
    parser.add_argument("--strategy", required=True, help="Strategy to test")
    parser.add_argument("--days", type=int, default=365, help="Days of historical data to fetch")
    parser.add_argument("--train-days", type=int, default=90, help="Length of training window (days)")
    parser.add_argument("--test-days", type=int, default=30, help="Length of testing window (days)")
    parser.add_argument("--metric", default="sortino_ratio", help="Metric to optimize for")
    parser.add_argument("--anchored", action="store_true", help="Use anchored expanding window for training")
    args = parser.parse_args()

    settings = load_settings()
    broker = BrokerFactory.create(
        config=settings,
        mode=TradingMode.PAPER,
    )
    
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=args.days)

    logger.info(f"Fetching {args.days} days of data for {args.symbol}...")
    
    strategy_cls = STRATEGY_CLASSES.get(args.strategy)
    if not strategy_cls:
        logger.error(f"Strategy {args.strategy} not found.")
        return
        
    strat_config = load_strategy_config(args.strategy)
    timeframe = strat_config.get("timeframe", "1Hour")
    
    param_grid = strat_config.get("param_grid", {})
    if not param_grid:
        logger.error(f"No 'param_grid' found in strategies.yaml for {args.strategy}.")
        logger.info("Example param_grid: param_grid: {atr_multiplier: [2.0, 2.5, 3.0]}")
        return

    # Create dummy strategy just to get timeframe mapping requirements if needed
    # (Though we already have the timeframe from config)
    
    feed = DataFeed(broker=broker, config=settings)
    df = feed.get_historical_range(
        args.symbol,
        start_time,
        end_time,
        timeframe=timeframe,
    )

    if df.empty:
        logger.error("No data fetched. Check symbol/timeframe.")
        return
        
    logger.info(f"Running WFA on {args.strategy} | Train: {args.train_days}d | Test: {args.test_days}d")
    
    engine = BacktestEngine(
        initial_capital=settings.get("trading", {}).get("capital", 10000.0)
    )
    
    wfa = WalkForwardOptimizer(
        engine=engine,
        train_days=args.train_days,
        test_days=args.test_days,
        anchored=args.anchored,
    )

    def factory(params: dict):
        return strategy_cls(params)

    # Base params is the config without the param_grid
    base_params = {k: v for k, v in strat_config.items() if k != "param_grid"}

    results = wfa.run_walk_forward(
        symbol=args.symbol,
        df=df,
        strategy_factory=factory,
        base_params=base_params,
        param_grid=param_grid,
        optimize_metric=args.metric,
    )
    
    print_wfa_results(results)


if __name__ == "__main__":
    main()
