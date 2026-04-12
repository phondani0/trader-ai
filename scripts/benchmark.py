"""
Benchmark Suite — Regime-Aware Strategy Stress Tester.
Runs all strategies against curated historical market regimes.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml
from loguru import logger

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.backtest.engine import BacktestEngine
from src.core.broker_factory import BrokerFactory
from src.core.data_feed import DataFeed
from src.core.models import TradingMode
from src.strategies.macd_bollinger import MACDBollingerStrategy
from src.strategies.vwap_reversion import VWAPReversionStrategy
from src.strategies.supertrend import SupertrendStrategy
from src.strategies.orb import ORBStrategy
from src.strategies.rsi_divergence import RSIDivergenceStrategy
from src.strategies.ema_crossover import EMACrossoverStrategy
from src.strategies.chart_patterns import ChartPatternStrategy


def load_regimes() -> list:
    path = Path(__file__).parent.parent / "config" / "regimes.yaml"
    with open(path) as f:
        return yaml.safe_load(f)["regimes"]


def init_strategies() -> list:
    path = Path(__file__).parent.parent / "config" / "strategies.yaml"
    with open(path) as f:
        config = yaml.safe_load(f)

    strategy_map = {
        "macd_bollinger": MACDBollingerStrategy,
        "vwap_reversion": VWAPReversionStrategy,
        "supertrend": SupertrendStrategy,
        "orb": ORBStrategy,
        "rsi_divergence": RSIDivergenceStrategy,
        "ema_crossover": EMACrossoverStrategy,
        "chart_patterns": ChartPatternStrategy,
    }

    strategies = []
    for name, cls in strategy_map.items():
        params = config.get(name, {"enabled": True})
        strategies.append(cls(params=params))
    return [s for s in strategies if s.enabled]


def run_benchmark(strategy_filter: str = None):
    regimes = load_regimes()
    strategies = init_strategies()

    if strategy_filter and strategy_filter != "all":
        strategies = [s for s in strategies if s.name == strategy_filter]

    if not strategies:
        print(f"No enabled strategies found matching filter: {strategy_filter}")
        return

    # Setup environment
    broker = BrokerFactory.create({"broker": {"name": "ccxt"}}, TradingMode.PAPER)
    data_feed = DataFeed(broker=broker, config={})
    engine = BacktestEngine(fee_pct=0.055, slippage_pct=0.1)

    results_matrix = []

    print("\n" + "═" * 100)
    print(f"{'STRATEGY BENCHMARK SUITE':^100}")
    print("═" * 100)

    for regime in regimes:
        name = regime["name"]
        symbol = regime["symbol"]
        
        # Handle trailing dates
        if regime["start_date"] == "trailing_30":
            start = datetime.utcnow() - timedelta(days=30)
            end = datetime.utcnow()
        else:
            start = datetime.strptime(regime["start_date"], "%Y-%m-%d")
            end = datetime.strptime(regime["end_date"], "%Y-%m-%d")

        print(f"\n▶ Testing Regime: {name} ({symbol})")
        print(f"  Range: {start.date()} to {end.date()}")
        print(f"  {regime['description']}")
        print("-" * 100)

        # Get Data
        df = data_feed.get_historical_range(symbol, start, end)
        if df.empty:
            print(f"  ⚠️ No data found for this period. Skipping.")
            continue

        for strat in strategies:
            result = engine.run(symbol, df, strat)
            
            results_matrix.append({
                "Regime": name,
                "Strategy": strat.name,
                "Trades": len(result.trades),
                "Win Rate": f"{result.win_rate_pct:.1f}%",
                "Net ROI": f"{result.net_roi_pct:+.2f}%",
                "MDD": f"{result.max_drawdown_pct:.1f}%",
                "ROI_Val": result.net_roi_pct # Hidden for sorting/logic
            })
            
            status = "✅" if result.net_roi_pct > 0 else "❌"
            print(f"  {status} {strat.name:<15} | ROI: {result.net_roi_pct:>7.2f}% | Win Rate: {result.win_rate_pct:>5.1f}% | Trades: {len(result.trades)}")

    # Final Matrix Table
    print("\n" + "═" * 100)
    print(f"{'FINAL PERFORMANCE MATRIX':^100}")
    print("═" * 100)
    
    matrix_df = pd.DataFrame(results_matrix)
    # Pivot for Heatmap Style
    pivot = matrix_df.pivot(index="Strategy", columns="Regime", values="Net ROI")
    print(pivot.to_string())
    print("═" * 100 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark strategies across curated crypto market regimes"
    )
    parser.add_argument("--strategy", default="all")
    args = parser.parse_args()

    run_benchmark(args.strategy)


if __name__ == "__main__":
    main()
