"""
Benchmark Suite — Regime-Aware Strategy Stress Tester.
Runs all strategies against curated historical market regimes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.backtest.engine import BacktestEngine
from src.core.broker_factory import BrokerFactory
from src.core.data_feed import DataFeed
from src.core.models import TradingMode
from src.strategies.chart_patterns import ChartPatternStrategy
from src.strategies.ema_crossover import EMACrossoverStrategy
from src.strategies.macd_bollinger import MACDBollingerStrategy
from src.strategies.orb import ORBStrategy
from src.strategies.rsi_divergence import RSIDivergenceStrategy
from src.strategies.supertrend import SupertrendStrategy
from src.strategies.vwap_reversion import VWAPReversionStrategy

PROJECT_ROOT = Path(__file__).parent.parent


def load_settings() -> dict:
    with open(PROJECT_ROOT / "config" / "settings.yaml", "r") as f:
        return yaml.safe_load(f)


def load_regimes() -> list:
    with open(PROJECT_ROOT / "config" / "regimes.yaml", "r") as f:
        return yaml.safe_load(f)["regimes"]


def init_strategies() -> list:
    with open(PROJECT_ROOT / "config" / "strategies.yaml", "r") as f:
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
    return [strategy for strategy in strategies if strategy.enabled]


def evaluate_strategy(summary, thresholds: dict) -> tuple[bool, str]:
    if len(summary.trades) < thresholds.get("min_trades", 3):
        return False, "not enough benchmark trades"
    if summary.killed_by_drawdown:
        return False, summary.kill_switch_reason or "drawdown kill-switch triggered"
    if abs(summary.max_drawdown_pct) > thresholds.get("max_drawdown_pct", 20.0):
        return False, "drawdown above approval threshold"
    if summary.net_roi_pct < thresholds.get("min_net_roi_pct", 0.0):
        return False, "net ROI below approval threshold"
    if summary.expectancy_pct < thresholds.get("min_expectancy_pct", 0.0):
        return False, "expectancy below approval threshold"
    if summary.profit_factor < thresholds.get("min_profit_factor", 1.0):
        return False, "profit factor below approval threshold"
    return True, "approved"


def run_benchmark(strategy_filter: str = None):
    settings = load_settings()
    regimes = load_regimes()
    strategies = init_strategies()
    benchmark_cfg = settings.get("benchmarking", {})
    risk_cfg = settings.get("risk_management", {})

    if strategy_filter and strategy_filter != "all":
        strategies = [strategy for strategy in strategies if strategy.name == strategy_filter]

    if not strategies:
        print(f"No enabled strategies found matching filter: {strategy_filter}")
        return None

    broker = BrokerFactory.create(
        {
            "broker": {"name": "ccxt"},
            "trading": {"asset_class": "crypto"},
            "risk_management": risk_cfg,
        },
        TradingMode.PAPER,
    )
    data_feed = DataFeed(broker=broker, config={})
    engine = BacktestEngine(
        fee_pct=0.055,
        slippage_pct=0.1,
        initial_capital=float(settings.get("trading", {}).get("capital", 10000)),
        max_drawdown_pct=float(risk_cfg.get("max_drawdown_pct", 20.0)),
    )

    results_matrix = []
    strategy_rollups: dict[str, dict] = {
        strategy.name: {
            "trades": [],
            "windows": [],
            "killed_by_drawdown": False,
            "kill_reasons": [],
        }
        for strategy in strategies
    }

    print("\n" + "═" * 120)
    print(f"{'STRATEGY BENCHMARK SUITE':^120}")
    print("═" * 120)

    for regime in regimes:
        name = regime["name"]
        symbol = regime["symbol"]

        if regime["start_date"] == "trailing_30":
            start = datetime.utcnow() - timedelta(days=30)
            end = datetime.utcnow()
        else:
            start = datetime.strptime(regime["start_date"], "%Y-%m-%d")
            end = datetime.strptime(regime["end_date"], "%Y-%m-%d")

        print(f"\n▶ Testing Regime: {name} ({symbol})")
        print(f"  Range: {start.date()} to {end.date()}")
        print(f"  {regime['description']}")
        print("-" * 120)

        df = data_feed.get_historical_range(symbol, start, end)
        if df.empty:
            print("  ⚠️ No data found for this period. Skipping.")
            continue

        for strategy in strategies:
            result = engine.run(symbol, df, strategy)
            rollup = strategy_rollups[strategy.name]
            rollup["trades"].extend(result.trades)
            rollup["windows"].append((result.period_start, result.period_end))
            if result.killed_by_drawdown:
                rollup["killed_by_drawdown"] = True
                if result.kill_switch_reason:
                    rollup["kill_reasons"].append(result.kill_switch_reason)

            results_matrix.append(
                {
                    "Regime": name,
                    "Strategy": strategy.name,
                    "Trades": len(result.trades),
                    "Win Rate": f"{result.win_rate_pct:.1f}%",
                    "Net ROI": f"{result.net_roi_pct:+.2f}%",
                    "Expectancy": f"{result.expectancy_pct:+.2f}%",
                    "Profit Factor": (
                        "inf" if result.profit_factor == float("inf") else f"{result.profit_factor:.2f}"
                    ),
                    "MDD": f"{result.max_drawdown_pct:.1f}%",
                    "Killed": "YES" if result.killed_by_drawdown else "NO",
                }
            )

            status = "✅" if result.net_roi_pct > 0 and not result.killed_by_drawdown else "❌"
            profit_factor = (
                "inf" if result.profit_factor == float("inf") else f"{result.profit_factor:.2f}"
            )
            print(
                f"  {status} {strategy.name:<15} | ROI: {result.net_roi_pct:>7.2f}% | "
                f"Win Rate: {result.win_rate_pct:>5.1f}% | "
                f"Expectancy: {result.expectancy_pct:>6.2f}% | "
                f"PF: {profit_factor:>5} | Trades: {len(result.trades):>3}"
            )

    print("\n" + "═" * 120)
    print(f"{'FINAL PERFORMANCE MATRIX':^120}")
    print("═" * 120)
    matrix_df = pd.DataFrame(results_matrix)
    if not matrix_df.empty:
        pivot = matrix_df.pivot(index="Strategy", columns="Regime", values="Net ROI")
        print(pivot.to_string())

    approvals = build_approvals_payload(engine, strategy_rollups, benchmark_cfg)
    approvals_path = PROJECT_ROOT / benchmark_cfg.get("approvals_path", "data/strategy_approvals.json")
    approvals_path.parent.mkdir(parents=True, exist_ok=True)
    with open(approvals_path, "w") as f:
        json.dump(approvals, f, indent=2)

    print("\n" + "═" * 120)
    print(f"{'DEPLOYMENT APPROVALS':^120}")
    print("═" * 120)
    for strategy_name, approval in approvals["strategies"].items():
        verdict = "APPROVED" if approval["approved"] else "BLOCKED"
        print(
            f"  {strategy_name:<15} | {verdict:<8} | ROI: {approval['metrics']['net_roi_pct']:>7.2f}% | "
            f"Expectancy: {approval['metrics']['expectancy_pct']:>6.2f}% | "
            f"PF: {approval['metrics']['profit_factor']:<6} | "
            f"MDD: {approval['metrics']['max_drawdown_pct']:>6.2f}% | "
            f"Reason: {approval['reason']}"
        )
    print("═" * 120)
    print(f"Approvals written to: {approvals_path}\n")

    return approvals


def build_approvals_payload(engine, strategy_rollups, benchmark_cfg):
    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "thresholds": benchmark_cfg,
        "strategies": {},
    }

    for strategy_name, rollup in strategy_rollups.items():
        trades = rollup["trades"]
        windows = rollup["windows"]
        if windows:
            period_start = min(window[0] for window in windows)
            period_end = max(window[1] for window in windows)
        else:
            period_start = datetime.utcnow()
            period_end = datetime.utcnow()

        summary = engine.summarize(
            symbol=strategy_name,
            period_start=period_start,
            period_end=period_end,
            trades=trades,
            killed_by_drawdown=rollup["killed_by_drawdown"],
            kill_reason=" | ".join(dict.fromkeys(rollup["kill_reasons"])),
        )
        approved, reason = evaluate_strategy(summary, benchmark_cfg)
        payload["strategies"][strategy_name] = {
            "approved": approved,
            "reason": reason,
            "metrics": {
                "trades": len(summary.trades),
                "net_roi_pct": round(summary.net_roi_pct, 4),
                "expectancy_pct": round(summary.expectancy_pct, 4),
                "profit_factor": (
                    "inf" if summary.profit_factor == float("inf") else round(summary.profit_factor, 4)
                ),
                "max_drawdown_pct": round(summary.max_drawdown_pct, 4),
                "win_rate_pct": round(summary.win_rate_pct, 4),
            },
        }

    return payload


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark strategies across curated crypto market regimes"
    )
    parser.add_argument("--strategy", default="all")
    args = parser.parse_args()

    run_benchmark(args.strategy)


if __name__ == "__main__":
    main()
