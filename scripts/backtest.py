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

    # Initialize strategies
    all_strategies = {
        "macd_bollinger": MACDBollingerStrategy({"enabled": True, "regime": ["all"],
                                                  "stop_loss_atr_multiplier": 1.5, "take_profit_rr_ratio": 2.0}),
        "vwap_reversion": VWAPReversionStrategy({"enabled": True, "regime": ["all"],
                                                  "vwap_std_entry": 2.0, "stop_loss_pct": 0.5,
                                                  "rsi_oversold": 30, "rsi_overbought": 70}),
        "supertrend": SupertrendStrategy({"enabled": True, "regime": ["all"], "volume_multiplier": 1.2}),
        "rsi_divergence": RSIDivergenceStrategy({"enabled": True, "regime": ["all"],
                                                  "lookback_bars": 30, "min_swing_pct": 0.5,
                                                  "require_macd_confirmation": True, "take_profit_rr_ratio": 2.0}),
        "ema_crossover": EMACrossoverStrategy({"enabled": True, "regime": ["all"],
                                                "fast_ema": 9, "slow_ema": 21, "adx_min": 25,
                                                "require_vwap_alignment": True, "trailing_stop_atr": 2.0}),
        "chart_patterns": ChartPatternStrategy({"enabled": True, "regime": ["all"],
                                                 "pivot_lookback": 5, "fib_tolerance": 0.05,
                                                 "stop_loss_pct": 1.0, "take_profit_rr_ratio": 2.0}),
    }

    strategies = list(all_strategies.values()) if strategy_name == "all" else [all_strategies[strategy_name]]

    # Walk-forward simulation
    min_lookback = 50  # Need at least 50 bars for indicators
    trades = []

    for i in range(min_lookback, len(df) - 1):
        window = df.iloc[:i + 1]
        regime = regime_detector.detect(window)

        for strategy in strategies:
            signal = strategy.generate_signal(symbol, window, regime)

            if signal:
                # Simulate trade: enter at signal price, exit at next bar's close (simplified)
                next_bar = df.iloc[i + 1]
                entry_price = signal.entry_price
                exit_price = float(next_bar["close"])

                # Check stop loss
                if signal.side == Side.BUY:
                    if float(next_bar["low"]) <= signal.stop_loss:
                        exit_price = signal.stop_loss  # Stopped out
                    elif signal.take_profit and float(next_bar["high"]) >= signal.take_profit:
                        exit_price = signal.take_profit  # Take profit
                else:
                    if float(next_bar["high"]) >= signal.stop_loss:
                        exit_price = signal.stop_loss
                    elif signal.take_profit and float(next_bar["low"]) <= signal.take_profit:
                        exit_price = signal.take_profit

                # Calculate P&L
                if signal.side == Side.BUY:
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = ((entry_price - exit_price) / entry_price) * 100

                trades.append({
                    "timestamp": window.index[-1],
                    "strategy": signal.strategy_name,
                    "side": signal.side.value,
                    "entry": entry_price,
                    "exit": exit_price,
                    "pnl_pct": pnl_pct,
                    "regime": regime.value,
                    "reason": signal.reason,
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest trading strategies")
    parser.add_argument("--symbol", default="SPY", help="Stock symbol to backtest")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backtest")
    parser.add_argument("--strategy", default="all", help="Strategy name or 'all'")
    parser.add_argument("--timeframe", default="5Min", help="Candle timeframe")
    args = parser.parse_args()

    run_backtest(args.symbol, args.days, args.strategy, args.timeframe)
