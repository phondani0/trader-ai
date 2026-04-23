"""
Backtesting Runner.

Runs strategies against historical data to validate performance.

Usage:
    python scripts/backtest.py --symbol BTC/USDT:USDT --days 90
    python scripts/backtest.py --symbol ETH/USDT:USDT --strategy vwap_reversion
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from dotenv import load_dotenv
from loguru import logger

from src.core.backtest.engine import BacktestEngine
from src.core.broker_factory import BrokerFactory
from src.core.data_feed import DataFeed
from src.core.models import TradingMode
from src.core.strategy_approvals import get_symbol_strategy_approvals
from src.strategies.bearish_breakdown_continuation import (
    BearishBreakdownContinuationStrategy,
)
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


def load_settings() -> dict:
    with open(PROJECT_ROOT / "config" / "settings.yaml", "r") as f:
        return yaml.safe_load(f)


def load_strategies() -> dict:
    with open(PROJECT_ROOT / "config" / "strategies.yaml", "r") as f:
        strat_config = yaml.safe_load(f)

    return {
        "macd_bollinger": MACDBollingerStrategy(strat_config.get("macd_bollinger", {})),
        "macd_bollinger_inverse": MACDBollingerInverseStrategy(strat_config.get("macd_bollinger_inverse", {})),
        "vwap_reversion": VWAPReversionStrategy(strat_config.get("vwap_reversion", {})),
        "range_reversion": RangeReversionStrategy(strat_config.get("range_reversion", {})),
        "supertrend": SupertrendStrategy(strat_config.get("supertrend", {})),
        "orb": ORBStrategy(strat_config.get("orb", {})),
        "rsi_divergence": RSIDivergenceStrategy(strat_config.get("rsi_divergence", {})),
        "ema_crossover": EMACrossoverStrategy(strat_config.get("ema_crossover", {})),
        "donchian_breakout": DonchianBreakoutStrategy(strat_config.get("donchian_breakout", {})),
        "bearish_breakdown_continuation": BearishBreakdownContinuationStrategy(
            strat_config.get("bearish_breakdown_continuation", {})
        ),
        "volume_weighted_tsmom": VolumeWeightedTSMomentumStrategy(
            strat_config.get("volume_weighted_tsmom", {})
        ),
        "chart_patterns": ChartPatternStrategy(strat_config.get("chart_patterns", {})),
        "episodic_pivot": EpisodicPivotStrategy(strat_config.get("episodic_pivot", {})),
        "episodic_pivot_short": EpisodicPivotShortStrategy(strat_config.get("episodic_pivot_short", {})),
    }


def load_strategy_approvals(settings: dict) -> dict:
    benchmark_cfg = settings.get("benchmarking", {})
    if not benchmark_cfg.get("enforce_approvals", True):
        return {}

    approvals_rel = benchmark_cfg.get("approvals_path", "data/strategy_approvals.json")
    approvals_path = PROJECT_ROOT / approvals_rel
    if not approvals_path.exists():
        logger.warning(
            f"Benchmark approvals file not found at {approvals_path}. "
            "Falling back to config-level strategy enablement."
        )
        return {}

    try:
        with open(approvals_path, "r") as f:
            payload = json.load(f)
        return payload
    except Exception as exc:
        logger.error(f"Failed to load strategy approvals: {exc}")
        return {}


def apply_strategy_approvals(
    strategies: list,
    approvals: dict,
    symbol: str | None = None,
) -> list:
    symbol_scoped = (
        symbol is not None
        and isinstance(approvals, dict)
        and isinstance(approvals.get("symbols"), dict)
    )
    scoped_approvals = (
        get_symbol_strategy_approvals(approvals, symbol)
        if symbol is not None
        else approvals
    )
    approved_strategies = []
    for strategy in strategies:
        if symbol_scoped and strategy.name not in scoped_approvals:
            logger.info(
                f"Skipping unbenchmarked strategy for {symbol} in live-style backtest: {strategy.name}"
            )
            continue
        if scoped_approvals and strategy.name not in scoped_approvals:
            logger.info(
                f"Skipping unbenchmarked strategy in live-style backtest: {strategy.name}"
            )
            continue

        approval = scoped_approvals.get(strategy.name, {})
        deployable_regimes = approval.get("deployable_regimes")
        deployable_market_profiles = approval.get("deployable_market_profiles")
        deployable_market_biases = approval.get("deployable_market_biases")
        if deployable_regimes is not None:
            strategy.set_benchmark_approved_regimes(deployable_regimes)
        if deployable_market_profiles is not None:
            strategy.set_benchmark_approved_market_profiles(
                deployable_market_profiles
            )
        if deployable_market_biases is not None:
            strategy.set_benchmark_approved_market_biases(
                deployable_market_biases
            )

        if approval and not approval.get("approved", False) and not deployable_regimes:
            logger.info(
                f"Skipping blocked strategy in live-style backtest: {strategy.name} "
                f"({approval.get('reason', 'not approved')})"
            )
            continue

        approved_strategies.append(strategy)

    return approved_strategies


def run_backtest(
    symbol: str,
    days: int | None,
    strategy_name: str = "all",
    from_date: str | None = None,
    to_date: str | None = None,
):
    """
    Run a backtest for a given symbol and strategy.
    """
    load_dotenv()

    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
    )

    settings = load_settings()
    if days is None:
        days = int(settings.get("data", {}).get("lookback_days", 30))

    logger.info(
        f"═══ Backtesting {symbol} — {days} days — {strategy_name} ═══"
    )
    risk_config = settings.get("risk_management", {})
    data_config = dict(settings.get("data", {}))
    data_config.update({"lookback_days": days})
    broker = BrokerFactory.create(
        {
            "broker": {"name": "ccxt"},
            "trading": {"asset_class": "crypto"},
            "data": data_config,
            "risk_management": risk_config,
        },
        TradingMode.PAPER,
    )
    data_feed = DataFeed(
        broker=broker,
        config={"data": data_config},
    )
    strategy_map = load_strategies()
    if strategy_name == "all":
        strategies = [strategy for strategy in strategy_map.values() if strategy.enabled]
        strategies = apply_strategy_approvals(
            strategies,
            load_strategy_approvals(settings),
            symbol=symbol,
        )
        if not strategies:
            logger.error("No benchmark-approved strategies available for live-style backtest")
            return None
        logger.info(
            "Live-style stack: "
            + ", ".join(strategy.name for strategy in strategies)
        )
        logger.info(
            "Strategy timeframes: "
            + ", ".join(
                f"{strategy.name}={data_feed.strategy_timeframe(strategy)}"
                for strategy in strategies
            )
        )
    else:
        strategies = [strategy_map[strategy_name]]
        logger.info(
            f"Strategy timeframe: {strategies[0].name}="
            f"{data_feed.strategy_timeframe(strategies[0])}"
        )

    if from_date or to_date:
        start = _parse_datetime(from_date) if from_date else datetime.utcnow() - timedelta(days=days)
        end = _parse_datetime(to_date) if to_date else datetime.utcnow()
        if strategy_name == "all":
            df = data_feed.get_historical_range_for_strategies(
                symbol,
                start,
                end,
                strategies=strategies,
            )
        else:
            df = data_feed.get_historical_range(
                symbol,
                start,
                end,
                strategies=strategies,
                timeframe=data_feed.strategy_timeframe(strategies[0]),
            )
    else:
        if strategy_name == "all":
            df = data_feed.refresh_for_strategies([symbol], strategies=strategies).get(
                symbol,
                {},
            )
        else:
            df = data_feed.get_data(
                symbol,
                force_refresh=True,
                strategies=strategies,
                timeframe=data_feed.strategy_timeframe(strategies[0]),
            )

    if isinstance(df, dict):
        if not any(not frame.empty for frame in df.values()):
            logger.error(f"No data for {symbol}")
            return None
        log_multitimeframe_summary(df)
        reference_df = data_feed.primary_frame(df)
    else:
        if df.empty:
            logger.error(f"No data for {symbol}")
            return None
        logger.info(f"Data: {len(df)} bars from {df.index[0]} to {df.index[-1]}")
        reference_df = df

    if not reference_df.empty:
        log_window_profile(data_feed.regime_detector.profile_window(reference_df))

    engine = BacktestEngine(
        fee_pct=0.055,
        slippage_pct=0.1,
        initial_capital=float(settings.get("trading", {}).get("capital", 10000)),
        max_drawdown_pct=float(risk_config.get("max_drawdown_pct", 20.0)),
    )

    if strategy_name == "all":
        result = engine.run_selector(symbol, df, strategies)
    else:
        result = engine.run(symbol, df, strategies[0])

    log_result(result)
    return result


def log_window_profile(profile: dict):
    logger.info(
        "Window Profile: "
        f"Market={profile.get('detected_market_profile')} ({profile.get('market_bias')}) | "
        f"Dominant Execution={profile.get('dominant_execution_regime')} | "
        f"Return={profile.get('window_return_pct', 0.0):+.2f}% | "
        f"Realized Vol={profile.get('realized_vol_pct', 0.0):.2f}% | "
        f"Median ADX={profile.get('median_adx', 0.0):.2f} | "
        f"Median ATR%={profile.get('median_atr_pct', 0.0):.2f}"
    )


def log_result(result):
    if result is None:
        return

    logger.info(f"\n{'═' * 60}")
    logger.info(f"BACKTEST RESULTS: {result.symbol}")
    logger.info(f"{'═' * 60}")
    logger.info(f"Period: {result.period_start} to {result.period_end}")
    logger.info(f"Total Trades: {len(result.trades)}")
    logger.info(f"Win Rate: {result.win_rate_pct:.1f}%")
    logger.info(
        f"Avg Win: {result.avg_win_pct:.2f}%"
        if result.avg_win_pct
        else "Avg Win: N/A"
    )
    logger.info(
        f"Avg Loss: {result.avg_loss_pct:.2f}%"
        if result.avg_loss_pct
        else "Avg Loss: N/A"
    )
    logger.info(f"{'-' * 60}")
    logger.info(f"GROSS Return (sum): {result.gross_roi_pct:.2f}%")
    logger.info(f"NET Return (compounded): {result.net_roi_pct:.2f}%")
    logger.info(f"Max Drawdown: {result.max_drawdown_pct:.2f}%")
    logger.info(f"Expectancy / Trade: {result.expectancy_pct:.2f}%")
    logger.info(
        "Profit Factor: inf"
        if result.profit_factor == float("inf")
        else f"Profit Factor: {result.profit_factor:.2f}"
    )
    logger.info(f"Final Equity: ${result.final_equity:,.2f}")
    if result.killed_by_drawdown:
        logger.info(f"Drawdown Kill-Switch: YES ({result.kill_switch_reason})")
    logger.info(f"{'-' * 60}")
    sharpe_str = f"{result.sharpe_ratio:.3f}" if result.sharpe_ratio != float("inf") else "inf"
    sortino_str = f"{result.sortino_ratio:.3f}" if result.sortino_ratio != float("inf") else "inf"
    calmar_str = f"{result.calmar_ratio:.3f}" if result.calmar_ratio != float("inf") else "inf"
    logger.info(f"Sharpe Ratio:  {sharpe_str}  (target: >1.5)")
    logger.info(f"Sortino Ratio: {sortino_str}  (target: >2.0)")
    logger.info(f"Calmar Ratio:  {calmar_str}  (target: >1.0)")
    if result.annualized_return_pct:
        logger.info(f"Ann. Return:   {result.annualized_return_pct:+.2f}%")
    if result.annualized_volatility_pct:
        logger.info(f"Ann. Volatility: {result.annualized_volatility_pct:.2f}%")
    logger.info(f"{'═' * 60}")

    if result.strategy_breakdown:
        logger.info("\nPer-Strategy Breakdown:")
        for strategy_name, metrics in result.strategy_breakdown.items():
            logger.info(
                f"  {strategy_name:<15} | Trades: {metrics['trades']:>3} | "
                f"Win Rate: {metrics['win_rate_pct']:>5.1f}% | "
                f"Net ROI: {metrics['net_roi_pct']:>7.2f}% | "
                f"Expectancy: {metrics['expectancy_pct']:>6.2f}%"
            )

    if result.regime_breakdown:
        logger.info("\nPer-Regime Breakdown:")
        for regime_name, metrics in result.regime_breakdown.items():
            logger.info(
                f"  {regime_name:<20} | Trades: {metrics['trades']:>3} | "
                f"Win Rate: {metrics['win_rate_pct']:>5.1f}% | "
                f"Net ROI: {metrics['net_roi_pct']:>7.2f}%"
            )


def log_multitimeframe_summary(data: dict[str, object]):
    parts = []
    for timeframe, frame in sorted(data.items()):
        if hasattr(frame, "empty") and not frame.empty:
            parts.append(
                f"{timeframe}: {len(frame)} bars from {frame.index[0]} to {frame.index[-1]}"
            )
    if parts:
        logger.info("Data windows: " + " | ".join(parts))


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d")


def main():
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Backtest trading strategies")
    parser.add_argument(
        "--symbol",
        default="BTC/USDT:USDT",
        help="Symbol to backtest (defaults to BTC/USDT:USDT)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help=(
            "Number of days to backtest "
            f"(defaults to config data.lookback_days={settings.get('data', {}).get('lookback_days', 30)})"
        ),
    )
    parser.add_argument("--strategy", default="all", help="Strategy name or 'all'")
    parser.add_argument(
        "--from",
        dest="from_date",
        help="Explicit UTC/ISO start date, e.g. 2024-01-01 or 2024-01-01T00:00:00",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        help="Explicit UTC/ISO end date, e.g. 2024-03-31 or 2024-03-31T00:00:00",
    )
    args = parser.parse_args()

    run_backtest(
        args.symbol,
        args.days,
        args.strategy,
        args.from_date,
        args.to_date,
    )


if __name__ == "__main__":
    main()
