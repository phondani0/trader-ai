"""
Backtesting Runner.

Runs strategies against historical data to validate performance.

Usage:
    python scripts/backtest.py --symbol BTC/USDT:USDT --days 90
    python scripts/backtest.py --symbol ETH/USDT:USDT --strategy vwap_reversion
"""

from __future__ import annotations

import argparse
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


def load_strategies() -> dict:
    with open(PROJECT_ROOT / "config" / "strategies.yaml", "r") as f:
        strat_config = yaml.safe_load(f)

    return {
        "macd_bollinger": MACDBollingerStrategy(strat_config.get("macd_bollinger", {})),
        "vwap_reversion": VWAPReversionStrategy(strat_config.get("vwap_reversion", {})),
        "supertrend": SupertrendStrategy(strat_config.get("supertrend", {})),
        "orb": ORBStrategy(strat_config.get("orb", {})),
        "rsi_divergence": RSIDivergenceStrategy(strat_config.get("rsi_divergence", {})),
        "ema_crossover": EMACrossoverStrategy(strat_config.get("ema_crossover", {})),
        "chart_patterns": ChartPatternStrategy(strat_config.get("chart_patterns", {})),
    }


def run_backtest(
    symbol: str,
    days: int,
    strategy_name: str = "all",
    timeframe: str = "5Min",
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

    logger.info(f"═══ Backtesting {symbol} — {days} days — {strategy_name} ═══")

    settings = load_settings()
    risk_config = settings.get("risk_management", {})
    data_config = dict(settings.get("data", {}))
    data_config.update({"candle_interval": timeframe, "lookback_days": days})
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
    else:
        strategies = [strategy_map[strategy_name]]

    if from_date or to_date:
        start = _parse_datetime(from_date) if from_date else datetime.utcnow() - timedelta(days=days)
        end = _parse_datetime(to_date) if to_date else datetime.utcnow()
        df = data_feed.get_historical_range(
            symbol,
            start,
            end,
            strategies=strategies,
        )
    else:
        df = data_feed.get_data(symbol, force_refresh=True, strategies=strategies)

    if df.empty:
        logger.error(f"No data for {symbol}")
        return None

    logger.info(f"Data: {len(df)} bars from {df.index[0]} to {df.index[-1]}")
    log_window_profile(data_feed.regime_detector.profile_window(df))

    engine = BacktestEngine(
        fee_pct=0.055,
        slippage_pct=0.1,
        initial_capital=float(settings.get("trading", {}).get("capital", 10000)),
        max_drawdown_pct=float(risk_config.get("max_drawdown_pct", 20.0)),
    )

    if strategy_name == "all":
        result = engine.run_portfolio(symbol, df, strategies)
    else:
        result = engine.run(symbol, df, strategies[0])

    log_result(result)
    return result


def log_window_profile(profile: dict):
    logger.info(
        "Window Profile: "
        f"Dominant Regime={profile.get('dominant_regime')} | "
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


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d")


def main():
    parser = argparse.ArgumentParser(description="Backtest trading strategies")
    parser.add_argument(
        "--symbol",
        default="BTC/USDT:USDT",
        help="Symbol to backtest (defaults to BTC/USDT:USDT)",
    )
    parser.add_argument("--days", type=int, default=30, help="Number of days to backtest")
    parser.add_argument("--strategy", default="all", help="Strategy name or 'all'")
    parser.add_argument("--timeframe", default="5Min", help="Candle timeframe")
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
        args.timeframe,
        args.from_date,
        args.to_date,
    )


if __name__ == "__main__":
    main()
