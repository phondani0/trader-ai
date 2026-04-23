"""
Benchmark strategies across curated real-market windows.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.backtest.engine import BacktestEngine
from src.core.broker_factory import BrokerFactory
from src.core.data_feed import DataFeed
from src.core.models import TradingMode
from src.strategies.bearish_breakdown_continuation import (
    BearishBreakdownContinuationStrategy,
)
from src.strategies.chart_patterns import ChartPatternStrategy
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.episodic_pivot import EpisodicPivotStrategy
from src.strategies.episodic_pivot_short import EpisodicPivotShortStrategy
from src.strategies.ema_crossover import EMACrossoverStrategy
from src.strategies.macd_bollinger import MACDBollingerStrategy
from src.strategies.macd_bollinger_inverse import MACDBollingerInverseStrategy
from src.strategies.orb import ORBStrategy
from src.strategies.range_reversion import RangeReversionStrategy
from src.strategies.rsi_divergence import RSIDivergenceStrategy
from src.strategies.supertrend import SupertrendStrategy
from src.strategies.volume_weighted_tsmom import VolumeWeightedTSMomentumStrategy
from src.strategies.vwap_reversion import VWAPReversionStrategy

PROJECT_ROOT = Path(__file__).parent.parent


def resolve_approvals_path(config: dict) -> Path:
    benchmark_cfg = config.get("benchmarking", {})
    approvals_rel = benchmark_cfg.get("approvals_path", "data/strategy_approvals.json")
    approvals_path = Path(approvals_rel)
    if not approvals_path.is_absolute():
        approvals_path = PROJECT_ROOT / approvals_path
    return approvals_path


def _parse_generated_at(raw_value) -> datetime | None:
    if not raw_value:
        return None

    try:
        parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def ensure_strategy_approvals(
    config: dict,
    runner= None,
    now: datetime | None = None,
) -> bool:
    benchmark_cfg = config.get("benchmarking", {})
    if not benchmark_cfg.get("enforce_approvals", True):
        return False
    if not benchmark_cfg.get("refresh_on_start", False):
        return False

    approvals_path = resolve_approvals_path(config)
    max_age_hours = float(benchmark_cfg.get("max_approval_age_hours", 24))
    runner = runner or run_benchmark
    now = now or datetime.utcnow()

    refresh_required = not approvals_path.exists()
    if not refresh_required:
        try:
            with open(approvals_path, "r") as f:
                payload = json.load(f)
        except Exception:
            refresh_required = True
        else:
            generated_at = _parse_generated_at(payload.get("generated_at"))
            if generated_at is None:
                refresh_required = True
            else:
                refresh_required = (now - generated_at) > timedelta(hours=max_age_hours)

    if not refresh_required:
        return False

    runner("all")
    return True


def load_settings() -> dict:
    with open(PROJECT_ROOT / "config" / "settings.yaml", "r") as f:
        return yaml.safe_load(f)


def load_regimes() -> list:
    with open(PROJECT_ROOT / "config" / "regimes.yaml", "r") as f:
        return yaml.safe_load(f)["regimes"]


def init_strategies(strategy_filter: str | None = None) -> list:
    with open(PROJECT_ROOT / "config" / "strategies.yaml", "r") as f:
        config = yaml.safe_load(f)

    strategy_map = {
        "macd_bollinger": MACDBollingerStrategy,
        "macd_bollinger_inverse": MACDBollingerInverseStrategy,
        "vwap_reversion": VWAPReversionStrategy,
        "range_reversion": RangeReversionStrategy,
        "supertrend": SupertrendStrategy,
        "episodic_pivot": EpisodicPivotStrategy,
        "episodic_pivot_short": EpisodicPivotShortStrategy,
        "orb": ORBStrategy,
        "rsi_divergence": RSIDivergenceStrategy,
        "ema_crossover": EMACrossoverStrategy,
        "donchian_breakout": DonchianBreakoutStrategy,
        "bearish_breakdown_continuation": BearishBreakdownContinuationStrategy,
        "volume_weighted_tsmom": VolumeWeightedTSMomentumStrategy,
        "chart_patterns": ChartPatternStrategy,
    }

    strategies = []
    for name, cls in strategy_map.items():
        params = config.get(name, {"enabled": True})
        strategies.append(cls(params=params))
    if strategy_filter and strategy_filter != "all":
        return [strategy for strategy in strategies if strategy.name == strategy_filter]
    return [strategy for strategy in strategies if strategy.enabled]


def simulate_strategy_window(engine, symbol: str, df: pd.DataFrame, strategy):
    """Benchmark strategies through the live-style selector path for consistency."""
    return engine.run_selector(symbol, df, [strategy])


def evaluate_strategy(summary, thresholds: dict) -> tuple[bool, str]:
    if len(summary.trades) < thresholds.get("min_trades", 1):
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


def new_rollup() -> dict:
    return {
        "trades": [],
        "windows": [],
        "window_names": [],
        "profiles": [],
        "killed_by_drawdown": False,
        "kill_reasons": [],
    }


def collect_rollup(rollup: dict, result, window_name: str, window_profile: dict):
    rollup["trades"].extend(result.trades)
    rollup["windows"].append((result.period_start, result.period_end))
    rollup["window_names"].append(window_name)
    rollup["profiles"].append(window_profile)
    if result.killed_by_drawdown:
        rollup["killed_by_drawdown"] = True
        if result.kill_switch_reason:
            rollup["kill_reasons"].append(result.kill_switch_reason)


def summarize_rollup(engine, symbol: str, rollup: dict):
    windows = rollup["windows"]
    if windows:
        period_start = min(window[0] for window in windows)
        period_end = max(window[1] for window in windows)
    else:
        period_start = datetime.utcnow()
        period_end = datetime.utcnow()

    return engine.summarize(
        symbol=symbol,
        period_start=period_start,
        period_end=period_end,
        trades=rollup["trades"],
        killed_by_drawdown=rollup["killed_by_drawdown"],
        kill_reason=" | ".join(dict.fromkeys(rollup["kill_reasons"])),
        run_monte_carlo=True,
    )


def result_metrics(summary) -> dict:
    return {
        "trades": len(summary.trades),
        "net_roi_pct": round(summary.net_roi_pct, 4),
        "expectancy_pct": round(summary.expectancy_pct, 4),
        "profit_factor": (
            "inf" if summary.profit_factor == float("inf") else round(summary.profit_factor, 4)
        ),
        "max_drawdown_pct": round(summary.max_drawdown_pct, 4),
        "win_rate_pct": round(summary.win_rate_pct, 4),
        "sharpe_ratio": round(summary.sharpe_ratio, 3),
        "sortino_ratio": (
            "inf" if summary.sortino_ratio == float("inf") else round(summary.sortino_ratio, 3)
        ),
        "calmar_ratio": round(summary.calmar_ratio, 3),
        "mc_expected_mdd_pct": round(summary.mc_expected_mdd_pct, 4),
        "mc_worst_95_mdd_pct": round(summary.mc_worst_95_mdd_pct, 4),
    }


def empty_metrics() -> dict:
    return {
        "trades": 0,
        "net_roi_pct": 0.0,
        "expectancy_pct": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_pct": 0.0,
        "win_rate_pct": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "calmar_ratio": 0.0,
        "mc_expected_mdd_pct": 0.0,
        "mc_worst_95_mdd_pct": 0.0,
    }


def resolve_window_market_profile(regime_cfg: dict, window_profile: dict) -> str:
    return regime_cfg.get("market_profile") or window_profile.get(
        "detected_market_profile",
        "mixed",
    )


def resolve_expected_execution_regimes(
    regime_cfg: dict,
    window_profile: dict,
) -> list[str]:
    configured = regime_cfg.get("expected_execution_regimes")
    if configured:
        return list(dict.fromkeys(configured))

    dominant = window_profile.get("dominant_execution_regime") or window_profile.get(
        "dominant_regime"
    )
    return [dominant] if dominant else []


def execution_alignment_pct(
    expected_execution_regimes: list[str],
    window_profile: dict,
) -> float:
    mix = window_profile.get("execution_regime_mix_pct", {}) or {}
    return round(
        sum(float(mix.get(regime, 0.0) or 0.0) for regime in expected_execution_regimes),
        2,
    )


def derive_strategy_deployment(
    strategy_name: str,
    strategy_targets: dict[str, list[str]],
    benchmark_windows: list[dict],
) -> dict:
    target_regimes = set(strategy_targets.get(strategy_name, []))
    deployable_regimes: set[str] = set()
    deployable_market_profiles: set[str] = set()
    deployable_market_biases: set[str] = set()
    approved_windows: list[str] = []
    reasons: list[str] = []

    for window in benchmark_windows:
        strategy_metrics = window["strategies"].get(strategy_name)
        if not strategy_metrics:
            continue

        if not window.get("deployment_eligible", True):
            if strategy_metrics.get("approved"):
                reasons.append("approved only in research/non-deployable windows")
            continue

        overlap = sorted(
            target_regimes.intersection(window.get("expected_execution_regimes", []))
        )
        if strategy_metrics.get("approved") and overlap:
            deployable_regimes.update(overlap)
            deployable_market_profiles.add(window.get("market_profile", "mixed"))
            deployable_market_biases.add(window.get("market_bias", "flat"))
            approved_windows.append(window["name"])
            continue

        if strategy_metrics.get("approved") and not overlap:
            reasons.append(
                "approved in benchmark window but no overlap with target execution regimes"
            )
        else:
            reasons.append(strategy_metrics.get("reason", "window not approved"))

    approved = bool(deployable_regimes)
    if approved:
        reason = f"approved via windows: {', '.join(approved_windows)}"
    else:
        reason = reasons[0] if reasons else "no approved benchmark windows"

    return {
        "approved": approved,
        "reason": reason,
        "deployable_regimes": sorted(deployable_regimes),
        "deployable_market_profiles": sorted(deployable_market_profiles),
        "deployable_market_biases": sorted(deployable_market_biases),
        "approved_windows": approved_windows,
    }


def log_window_profile(profile: dict):
    execution_mix = profile.get("execution_regime_mix_pct", {}) or {}
    mix_items = sorted(
        execution_mix.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    mix_summary = ", ".join(
        f"{name}={pct:.1f}%"
        for name, pct in mix_items[:4]
    ) or "-"
    print(
        "  Market Profile: "
        f"Detected={profile['detected_market_profile']} ({profile['market_bias']}) | "
        f"Return={profile['window_return_pct']:+.2f}% | "
        f"Realized Vol={profile['realized_vol_pct']:.2f}% | "
        f"Median ADX={profile['median_adx']:.2f} | "
        f"Median ATR%={profile['median_atr_pct']:.2f} | "
        f"Trend Efficiency={profile['trend_efficiency_pct']:.2f}% | "
        f"Breakout Rate={profile['breakout_rate_pct']:.2f}%"
    )
    print(f"  Observed Execution Mix: {mix_summary}")


def summarize_selected_rollups(
    engine,
    symbol: str,
    strategy_window_rollups: dict[str, dict],
    selected_windows: list[str],
):
    combined = new_rollup()
    for window_name in selected_windows:
        rollup = strategy_window_rollups.get(window_name)
        if not rollup:
            continue
        combined["trades"].extend(rollup["trades"])
        combined["windows"].extend(rollup["windows"])
        combined["window_names"].extend(rollup["window_names"])
        combined["profiles"].extend(rollup["profiles"])
        if rollup["killed_by_drawdown"]:
            combined["killed_by_drawdown"] = True
        combined["kill_reasons"].extend(rollup["kill_reasons"])

    if not combined["windows"]:
        return None

    return summarize_rollup(engine, symbol, combined)


def run_benchmark(
    strategy_filter: str = None,
):
    settings = load_settings()
    regimes = load_regimes()
    strategies = init_strategies(strategy_filter)
    benchmark_cfg = settings.get("benchmarking", {})
    risk_cfg = settings.get("risk_management", {})

    if strategy_filter and strategy_filter != "all":
        strategies = [
            strategy for strategy in strategies if strategy.name == strategy_filter
        ]

    if not strategies:
        print(f"No enabled strategies found matching filter: {strategy_filter}")
        return None

    strategy_targets = {
        strategy.name: [regime.value for regime in strategy.supported_regimes]
        for strategy in strategies
    }

    broker = BrokerFactory.create(
        {
            "broker": {"name": "ccxt"},
            "trading": {"asset_class": "crypto"},
            "data": settings.get("data", {}),
            "risk_management": risk_cfg,
        },
        TradingMode.PAPER,
    )
    data_feed = DataFeed(
        broker=broker,
        config={"data": settings.get("data", {})},
    )
    engine = BacktestEngine(
        fee_pct=0.055,
        slippage_pct=0.1,
        initial_capital=float(settings.get("trading", {}).get("capital", 10000)),
        max_drawdown_pct=float(risk_cfg.get("max_drawdown_pct", 20.0)),
    )

    results_matrix = []
    benchmark_windows: list[dict] = []
    strategy_rollups = {strategy.name: new_rollup() for strategy in strategies}
    strategy_window_rollups = {strategy.name: {} for strategy in strategies}
    market_profile_rollups = {strategy.name: {} for strategy in strategies}

    print("\n" + "═" * 120)
    print(f"{'STRATEGY BENCHMARK SUITE':^120}")
    print("═" * 120)
    strategy_timeframes = ", ".join(
        sorted({str(strategy.timeframe or settings.get("data", {}).get("candle_interval", "5Min")) for strategy in strategies})
    )
    print(
        "Reference Profile Timeframe: "
        f"{settings.get('data', {}).get('candle_interval', '5Min')} | "
        f"Strategy Timeframes: {strategy_timeframes}"
    )

    for regime in regimes:
        name = regime["name"]
        symbol = regime["symbol"]

        if str(regime["start_date"]).startswith("trailing_"):
            trailing_days = int(str(regime["start_date"]).split("_", 1)[1])
            start = datetime.utcnow() - timedelta(days=trailing_days)
            end = datetime.utcnow()
        else:
            start = datetime.strptime(regime["start_date"], "%Y-%m-%d")
            end = datetime.strptime(regime["end_date"], "%Y-%m-%d")

        print(f"\n▶ Testing Regime: {name} ({symbol})")
        print(f"  Range: {start.date()} to {end.date()}")
        print(f"  {regime['description']}")
        print("-" * 120)

        ref_tf = settings.get("data", {}).get("candle_interval", "30Min")
        
        # ─── Macro Pre-warm (L1.10 Hardening) ───
        # Fetch 30 days of lead-in history to ensure EMA1000/Bias filters have converged
        # before the benchmark window starts.
        lead_in_start = start - timedelta(days=30)
        
        df_full = data_feed.get_historical_range(
            symbol,
            lead_in_start,
            end,
            timeframe=ref_tf,
        )
        if df_full.empty:
            print("  ⚠️ No reference data found for this period. Skipping.")
            continue

        # Trim to the target benchmark window AFTER indicator calculation
        ref_df = df_full[df_full.index >= pd.Timestamp(start)]
        if ref_df.empty:
            print("  ⚠️ No reference data found in the target window. Skipping.")
            continue

        window_profile = data_feed.regime_detector.profile_window(ref_df)
        market_profile = resolve_window_market_profile(regime, window_profile)
        expected_execution_regimes = resolve_expected_execution_regimes(
            regime,
            window_profile,
        )
        log_window_profile(window_profile)
        aligned_execution_pct = execution_alignment_pct(
            expected_execution_regimes,
            window_profile,
        )
        print(
            "  Curated Benchmark Intent: "
            f"Market={market_profile} | "
            f"Expected Execution={', '.join(expected_execution_regimes) or '-'} | "
            f"Observed Alignment={aligned_execution_pct:.1f}%"
        )

        window_record = {
            "name": name,
            "symbol": symbol,
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"),
            "description": regime["description"],
            "deployment_eligible": regime.get("deployment_eligible", True),
            "market_profile": market_profile,
            "market_bias": window_profile.get("market_bias", "flat"),
            "expected_execution_regimes": expected_execution_regimes,
            "aligned_execution_pct": aligned_execution_pct,
            "profile": window_profile,
            "strategies": {},
        }

        for strategy in strategies:
            st_tf = strategy.params.get("timeframe") or ref_tf
            st_df = data_feed.get_historical_range(
                symbol,
                start,
                end,
                timeframe=st_tf,
                strategies=[strategy],
            )
            if st_df.empty:
                print(f"  ⚠️ No data found for {strategy.name} on {st_tf}. Skipping.")
                continue

            result = simulate_strategy_window(engine, symbol, st_df, strategy)
            collect_rollup(
                strategy_rollups[strategy.name],
                result,
                name,
                window_profile,
            )
            strategy_window_rollups[strategy.name][name] = {
                "trades": list(result.trades),
                "windows": [(result.period_start, result.period_end)],
                "window_names": [name],
                "profiles": [window_profile],
                "killed_by_drawdown": result.killed_by_drawdown,
                "kill_reasons": [result.kill_switch_reason] if result.kill_switch_reason else [],
            }

            profile_bucket = market_profile_rollups[strategy.name].setdefault(
                market_profile,
                new_rollup(),
            )
            collect_rollup(profile_bucket, result, name, window_profile)

            approved_for_window, window_reason = evaluate_strategy(result, benchmark_cfg)
            metrics = result_metrics(result)
            metrics["killed_by_drawdown"] = result.killed_by_drawdown
            metrics["reason"] = window_reason
            metrics["approved"] = approved_for_window
            window_record["strategies"][strategy.name] = metrics

            results_matrix.append(
                {
                    "Window": name,
                    "Market Profile": market_profile,
                    "Observed Alignment %": f"{aligned_execution_pct:.1f}%",
                    "Strategy": strategy.name,
                    "Trades": len(result.trades),
                    "Win Rate": f"{result.win_rate_pct:.1f}%",
                    "Net ROI": f"{result.net_roi_pct:+.2f}%",
                    "Expectancy": f"{result.expectancy_pct:+.2f}%",
                    "Profit Factor": (
                        "inf"
                        if result.profit_factor == float("inf")
                        else f"{result.profit_factor:.2f}"
                    ),
                    "MDD": f"{result.max_drawdown_pct:.1f}%",
                    "Sharpe": f"{result.sharpe_ratio:.2f}",
                    "Sortino": (
                        "inf"
                        if result.sortino_ratio == float("inf")
                        else f"{result.sortino_ratio:.2f}"
                    ),
                    "Killed": "YES" if result.killed_by_drawdown else "NO",
                }
            )

            status = "✅" if approved_for_window else "❌"
            profit_factor = (
                "inf"
                if result.profit_factor == float("inf")
                else f"{result.profit_factor:.2f}"
            )
            sharpe_display = f"{result.sharpe_ratio:.2f}"
            print(
                f"  {status} {strategy.name:<15} | ROI: {result.net_roi_pct:>7.2f}% | "
                f"Win Rate: {result.win_rate_pct:>5.1f}% | "
                f"Expectancy: {result.expectancy_pct:>6.2f}% | "
                f"PF: {profit_factor:>5} | Sharpe: {sharpe_display:>6} | Trades: {len(result.trades):>3}"
            )

        benchmark_windows.append(window_record)

    print("\n" + "═" * 120)
    print(f"{'FINAL PERFORMANCE MATRIX':^120}")
    print("═" * 120)
    matrix_df = pd.DataFrame(results_matrix)
    if not matrix_df.empty:
        pivot = matrix_df.pivot(index="Strategy", columns="Window", values="Net ROI")
        print(pivot.to_string())

    from src.core.backtest.plot import plot_equity_curve
    from src.core.backtest.export import export_trades_to_csv
    plots_dir = PROJECT_ROOT / "data" / "plots"
    exports_dir = PROJECT_ROOT / "data" / "exports"

    market_profile_capabilities: dict[str, dict] = {}
    for strategy in strategies:
        # Process the global summary for the strategy (across all windows)
        global_rollup = strategy_rollups.get(strategy.name)
        if global_rollup:
            global_summary = summarize_rollup(engine, strategy.name, global_rollup)
            plot_equity_curve(
                global_summary, 
                output_dir=plots_dir, 
                filename=f"benchmark_{strategy.name}_global.png"
            )
            export_trades_to_csv(
                global_summary.trades,
                exports_dir / f"benchmark_{strategy.name}_global_trades.csv"
            )

        capabilities: dict[str, dict] = {}
        for profile_name, rollup in market_profile_rollups[strategy.name].items():
            summary = summarize_rollup(engine, strategy.name, rollup)
            approved, reason = evaluate_strategy(summary, benchmark_cfg)
            capabilities[profile_name] = {
                "approved": approved,
                "reason": reason,
                "window_names": rollup["window_names"],
                "metrics": result_metrics(summary),
            }
        market_profile_capabilities[strategy.name] = capabilities

    approvals = build_approvals_payload(
        engine,
        strategy_rollups,
        strategy_window_rollups,
        market_profile_capabilities,
        strategy_targets,
        benchmark_cfg,
        benchmark_windows,
    )
    approvals_path = resolve_approvals_path(settings)
    approvals_path.parent.mkdir(parents=True, exist_ok=True)
    if strategy_filter and strategy_filter != "all" and approvals_path.exists():
        try:
            with open(approvals_path, "r") as f:
                existing_payload = json.load(f)
        except Exception:
            existing_payload = None
        approvals = merge_approvals_payload(
            existing_payload,
            approvals,
            {strategy.name for strategy in strategies},
        )
    with open(approvals_path, "w") as f:
        json.dump(approvals, f, indent=2)

    print("\n" + "═" * 120)
    print(f"{'DEPLOYMENT APPROVALS':^120}")
    print("═" * 120)
    for strategy_name, approval in approvals["strategies"].items():
        verdict = "APPROVED" if approval["approved"] else "BLOCKED"
        deployable = ", ".join(approval["deployable_regimes"]) or "-"
        display_metrics = (
            approval["deployable_metrics"] if approval["approved"] else approval["global_metrics"]
        )
        sharpe_val = display_metrics.get('sharpe_ratio', 0.0)
        sortino_val = display_metrics.get('sortino_ratio', 0.0)
        sharpe_s = f"{sharpe_val:.2f}" if sharpe_val != "inf" else "inf"
        sortino_s = f"{sortino_val:.2f}" if sortino_val != "inf" else "inf"
        mc_mdd_s = f"{display_metrics.get('mc_worst_95_mdd_pct', 0.0):.2f}%"
        print(
            f"  {strategy_name:<15} | {verdict:<8} | ROI: {display_metrics['net_roi_pct']:>7.2f}% | "
            f"Expectancy: {display_metrics['expectancy_pct']:>6.2f}% | "
            f"PF: {display_metrics['profit_factor']:<6} | "
            f"MDD: {display_metrics['max_drawdown_pct']:>6.2f}% | "
            f"MC 95% MDD: {mc_mdd_s:>7} | "
            f"Sharpe: {sharpe_s:>6} | Sortino: {sortino_s:>6} | "
            f"Deployable: {deployable} | "
            f"Reason: {approval['reason']}"
        )
    print("═" * 120)
    print(f"Approvals written to: {approvals_path}\n")

    return approvals


def build_approvals_payload(
    engine,
    strategy_rollups: dict[str, dict],
    strategy_window_rollups: dict[str, dict],
    market_profile_capabilities: dict[str, dict],
    strategy_targets: dict[str, list[str]],
    benchmark_cfg: dict,
    benchmark_windows: list[dict],
) -> dict:
    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "thresholds": benchmark_cfg,
        "benchmark_windows": benchmark_windows,
        "market_profile_capabilities": market_profile_capabilities,
        "strategies": {},
        "symbols": {},
    }

    for strategy_name, rollup in strategy_rollups.items():
        global_summary = summarize_rollup(engine, strategy_name, rollup)
        deployment = derive_strategy_deployment(
            strategy_name,
            strategy_targets,
            benchmark_windows,
        )
        deployable_summary = summarize_selected_rollups(
            engine,
            strategy_name,
            strategy_window_rollups.get(strategy_name, {}),
            deployment["approved_windows"],
        )

        payload["strategies"][strategy_name] = {
            "approved": deployment["approved"],
            "reason": deployment["reason"],
            "target_regimes": strategy_targets.get(strategy_name, []),
            "deployable_regimes": deployment["deployable_regimes"],
            "deployable_market_profiles": deployment["deployable_market_profiles"],
            "deployable_market_biases": deployment["deployable_market_biases"],
            "approved_windows": deployment["approved_windows"],
            "global_metrics": result_metrics(global_summary),
            "deployable_metrics": (
                result_metrics(deployable_summary)
                if deployable_summary is not None
                else empty_metrics()
            ),
            "metrics": (
                result_metrics(deployable_summary)
                if deployment["approved"] and deployable_summary is not None
                else result_metrics(global_summary)
            ),
        }

    benchmark_symbols = sorted(
        {
            window["symbol"]
            for window in benchmark_windows
            if window.get("symbol")
        }
    )
    for symbol in benchmark_symbols:
        symbol_windows = [
            window for window in benchmark_windows if window.get("symbol") == symbol
        ]
        symbol_payload = {"strategies": {}}
        for strategy_name, rollup in strategy_rollups.items():
            symbol_window_names = [window["name"] for window in symbol_windows]
            symbol_summary = summarize_selected_rollups(
                engine,
                strategy_name,
                strategy_window_rollups.get(strategy_name, {}),
                symbol_window_names,
            )
            if symbol_summary is None:
                continue

            deployment = derive_strategy_deployment(
                strategy_name,
                strategy_targets,
                symbol_windows,
            )
            deployable_summary = summarize_selected_rollups(
                engine,
                strategy_name,
                strategy_window_rollups.get(strategy_name, {}),
                deployment["approved_windows"],
            )
            symbol_payload["strategies"][strategy_name] = {
                "approved": deployment["approved"],
                "reason": deployment["reason"],
                "target_regimes": strategy_targets.get(strategy_name, []),
                "deployable_regimes": deployment["deployable_regimes"],
                "deployable_market_profiles": deployment["deployable_market_profiles"],
                "deployable_market_biases": deployment["deployable_market_biases"],
                "approved_windows": deployment["approved_windows"],
                "global_metrics": result_metrics(symbol_summary),
                "deployable_metrics": (
                    result_metrics(deployable_summary)
                    if deployable_summary is not None
                    else empty_metrics()
                ),
                "metrics": (
                    result_metrics(deployable_summary)
                    if deployment["approved"] and deployable_summary is not None
                    else result_metrics(symbol_summary)
                ),
            }
        payload["symbols"][symbol] = symbol_payload

    return payload


def merge_approvals_payload(
    existing_payload: dict | None,
    new_payload: dict,
    strategy_names: set[str],
) -> dict:
    """Preserve untouched strategies when a benchmark run targets a subset."""
    if not existing_payload:
        return new_payload

    merged = json.loads(json.dumps(existing_payload))
    merged["generated_at"] = new_payload.get("generated_at", merged.get("generated_at"))
    merged["thresholds"] = new_payload.get("thresholds", merged.get("thresholds", {}))

    existing_windows = {
        (window.get("name"), window.get("symbol")): window
        for window in merged.get("benchmark_windows", [])
    }
    for window in new_payload.get("benchmark_windows", []):
        key = (window.get("name"), window.get("symbol"))
        if key not in existing_windows:
            existing_windows[key] = window
            continue
        existing_window = existing_windows[key]
        existing_window.setdefault("strategies", {})
        for strategy_name in strategy_names:
            strategy_metrics = window.get("strategies", {}).get(strategy_name)
            if strategy_metrics is not None:
                existing_window["strategies"][strategy_name] = strategy_metrics

    merged["benchmark_windows"] = list(existing_windows.values())

    merged.setdefault("market_profile_capabilities", {})
    for strategy_name in strategy_names:
        capability = new_payload.get("market_profile_capabilities", {}).get(strategy_name)
        if capability is not None:
            merged["market_profile_capabilities"][strategy_name] = capability

    merged.setdefault("strategies", {})
    for strategy_name in strategy_names:
        strategy_payload = new_payload.get("strategies", {}).get(strategy_name)
        if strategy_payload is not None:
            merged["strategies"][strategy_name] = strategy_payload

    merged.setdefault("symbols", {})
    for symbol, symbol_payload in new_payload.get("symbols", {}).items():
        merged["symbols"].setdefault(symbol, {})
        merged["symbols"][symbol].setdefault("strategies", {})
        for strategy_name in strategy_names:
            strategy_payload = symbol_payload.get("strategies", {}).get(strategy_name)
            if strategy_payload is not None:
                merged["symbols"][symbol]["strategies"][strategy_name] = strategy_payload

    return merged


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark strategies across curated crypto market regimes"
    )
    parser.add_argument("--strategy", default="all")
    args = parser.parse_args()

    run_benchmark(args.strategy)


if __name__ == "__main__":
    main()
