"""
Backtesting Runner.

Runs strategies against historical data to validate performance.

Usage:
    python scripts/backtest.py --symbol AAPL --days 90
    python scripts/backtest.py --symbol SPY --strategy macd_bollinger
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

from src.core.broker import AlpacaBroker
from src.core.data_feed import DataFeed
from src.core.models import TradingMode, MarketRegime, Side
from src.intelligence.regime_detector import RegimeDetector
from src.strategies.macd_bollinger import MACDBollingerStrategy
from src.strategies.vwap_reversion import VWAPReversionStrategy
from src.strategies.supertrend import SupertrendStrategy
from src.strategies.orb import ORBStrategy
from src.strategies.rsi_divergence import RSIDivergenceStrategy
from src.strategies.ema_crossover import EMACrossoverStrategy
from src.strategies.chart_patterns import ChartPatternStrategy


def run_backtest(symbol: str, days: int, strategy_name: str = "all", timeframe: str = "5Min"):
    """
    Run a backtest for a given symbol and strategy.
    """
    load_dotenv()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

    logger.info(f"═══ Backtesting {symbol} — {days} days — {strategy_name} ═══")

    # Get data
    broker = AlpacaBroker(mode=TradingMode.PAPER)
    config = {
        "data": {"candle_interval": timeframe, "lookback_days": days},
    }
    data_feed = DataFeed(broker=broker, config=config)
    df = data_feed.get_data(symbol, force_refresh=True)

    if df.empty:
        logger.error(f"No data for {symbol}")
        return

    logger.info(f"Data: {len(df)} bars from {df.index[0]} to {df.index[-1]}")

    # Regime detector
    regime_detector = RegimeDetector()

    # Initialize strategies from config file
    import yaml
    config_path = Path(__file__).parent.parent / "config" / "strategies.yaml"
    with open(config_path, "r") as f:
        strat_config = yaml.safe_load(f)

    all_strategies = {
        "macd_bollinger": MACDBollingerStrategy(strat_config.get("macd_bollinger", {})),
        "vwap_reversion": VWAPReversionStrategy(strat_config.get("vwap_reversion", {})),
        "supertrend": SupertrendStrategy(strat_config.get("supertrend", {})),
        "orb": ORBStrategy(strat_config.get("orb", {})),
        "rsi_divergence": RSIDivergenceStrategy(strat_config.get("rsi_divergence", {})),
        "ema_crossover": EMACrossoverStrategy(strat_config.get("ema_crossover", {})),
        "chart_patterns": ChartPatternStrategy(strat_config.get("chart_patterns", {})),
    }

    strategies = list(all_strategies.values()) if strategy_name == "all" else [all_strategies[strategy_name]]

    # Walk-forward simulation
    min_lookback = 50  # Need at least 50 bars for indicators
    trades = []
    
    # State tracking per strategy to ensure we don't exit instantaneously
    current_positions = {strat.name: None for strat in strategies}

    for i in range(min_lookback, len(df)):
        window = df.iloc[:i + 1]
        current_bar = df.iloc[i]
        regime = regime_detector.detect(window)

        for strategy in strategies:
            pos = current_positions[strategy.name]

            if pos is None:
                # Look for new entry
                if strategy.should_run(regime):
                    signal = strategy.generate_signal(symbol, window, regime)
                    if signal:
                        current_positions[strategy.name] = {
                        "entry_price": signal.entry_price,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                        "side": signal.side,
                        "entry_time": window.index[-1],
                        "regime": regime.value,
                        "reason": signal.reason,
                        "strategy": signal.strategy_name
                    }
            else:
                # Check for exit on current bar
                high = float(current_bar["high"])
                low = float(current_bar["low"])
                exit_price = None

                if pos["side"] == Side.BUY:
                    if low <= pos["stop_loss"]:
                        exit_price = pos["stop_loss"]  # Stopped out
                    elif pos["take_profit"] and high >= pos["take_profit"]:
                        exit_price = pos["take_profit"]  # Take profit
                else:
                    if high >= pos["stop_loss"]:
                        exit_price = pos["stop_loss"]
                    elif pos["take_profit"] and low <= pos["take_profit"]:
                        exit_price = pos["take_profit"]

                if exit_price is not None:
                    # Calculate P&L
                    if pos["side"] == Side.BUY:
                        pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
                    else:
                        pnl_pct = ((pos["entry_price"] - exit_price) / pos["entry_price"]) * 100

                    trades.append({
                        "timestamp": pos["entry_time"],
                        "exit_time": window.index[-1],
                        "strategy": pos["strategy"],
                        "side": pos["side"].value,
                        "entry": pos["entry_price"],
                        "exit": exit_price,
                        "pnl_pct": pnl_pct,
                        "regime": pos["regime"],
                        "reason": pos["reason"],
                    })
                    # Clear position
                    current_positions[strategy.name] = None
                    
    # Force close any remaining open positions at the end of backtest
    last_close = float(df.iloc[-1]["close"])
    last_time = df.index[-1]
    for strat_name, pos in current_positions.items():
        if pos is not None:
            if pos["side"] == Side.BUY:
                pnl_pct = ((last_close - pos["entry_price"]) / pos["entry_price"]) * 100
            else:
                pnl_pct = ((pos["entry_price"] - last_close) / pos["entry_price"]) * 100
            trades.append({
                "timestamp": pos["entry_time"],
                "exit_time": last_time,
                "strategy": pos["strategy"],
                "side": pos["side"].value,
                "entry": pos["entry_price"],
                "exit": last_close,
                "pnl_pct": pnl_pct,
                "regime": pos["regime"],
                "reason": pos["reason"] + " (EOD Close)",
            })

    # Results
    if not trades:
        logger.info("No trades generated during backtest period")
        return

    trades_df = pd.DataFrame(trades)
    logger.info(f"\n{'═' * 60}")
    logger.info(f"BACKTEST RESULTS: {symbol}")
    logger.info(f"{'═' * 60}")
    logger.info(f"Period: {df.index[0]} to {df.index[-1]}")
    logger.info(f"Total Trades: {len(trades_df)}")

    winning = trades_df[trades_df["pnl_pct"] > 0]
    losing = trades_df[trades_df["pnl_pct"] < 0]

    logger.info(f"Winning: {len(winning)} | Losing: {len(losing)}")
    logger.info(f"Win Rate: {len(winning) / len(trades_df) * 100:.1f}%")
    logger.info(f"Avg Win: {winning['pnl_pct'].mean():.2f}%" if len(winning) > 0 else "Avg Win: N/A")
    logger.info(f"Avg Loss: {losing['pnl_pct'].mean():.2f}%" if len(losing) > 0 else "Avg Loss: N/A")
    logger.info(f"Total Return: {trades_df['pnl_pct'].sum():.2f}%")
    logger.info(f"Max Drawdown: {trades_df['pnl_pct'].min():.2f}%")

    # Per-strategy breakdown
    logger.info(f"\nPer-Strategy Breakdown:")
    for strat_name, group in trades_df.groupby("strategy"):
        w = group[group["pnl_pct"] > 0]
        logger.info(
            f"  {strat_name}: {len(group)} trades | "
            f"Win Rate: {len(w) / len(group) * 100:.0f}% | "
            f"Total: {group['pnl_pct'].sum():.2f}%"
        )

    # Per-regime breakdown
    logger.info(f"\nPer-Regime Breakdown:")
    for reg, group in trades_df.groupby("regime"):
        w = group[group["pnl_pct"] > 0]
        logger.info(
            f"  {reg}: {len(group)} trades | "
            f"Win Rate: {len(w) / len(group) * 100:.0f}% | "
            f"Total: {group['pnl_pct'].sum():.2f}%"
        )

    return trades_df


def main():
    parser = argparse.ArgumentParser(description="Backtest trading strategies")
    parser.add_argument("--symbol", default=None, help="Symbol to backtest (defaults to SPY for equity or BTC/USD for crypto)")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backtest")
    parser.add_argument("--strategy", default="all", help="Strategy name or 'all'")
    parser.add_argument("--timeframe", default="5Min", help="Candle timeframe")
    args = parser.parse_args()

    import yaml
    config_path = Path(__file__).parent.parent / "config" / "settings.yaml"
    try:
        with open(config_path, "r") as f:
            settings = yaml.safe_load(f)
        asset_class = settings.get("trading", {}).get("asset_class", "equity").lower()
    except Exception:
        asset_class = "equity"
        
    if args.symbol is None:
        args.symbol = "BTC/USD" if asset_class == "crypto" else "SPY"

    run_backtest(args.symbol, args.days, args.strategy, args.timeframe)

if __name__ == "__main__":
    main()
