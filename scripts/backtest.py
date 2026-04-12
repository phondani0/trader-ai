"""
Backtesting Runner.

Runs strategies against historical data to validate performance.

Usage:
    python scripts/backtest.py --symbol BTC/USDT:USDT --days 90
    python scripts/backtest.py --symbol ETH/USDT:USDT --strategy vwap_reversion
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

from src.core.broker_factory import BrokerFactory
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
    broker = BrokerFactory.create({
        "broker": {"name": "ccxt"},
        "trading": {"asset_class": "crypto"},
        "data": {"candle_interval": timeframe, "lookback_days": days},
    }, TradingMode.PAPER)
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
    min_lookback = 50
    trades = []
    current_positions = {strat.name: None for strat in strategies}

    # Friction settings
    FEE_PCT = 0.055 / 100  # Bybit Taker Fee (0.055%)
    SLIPPAGE_PCT = 0.1 / 100  # Momentum Slippage (0.1%)
    TOTAL_FRICTION = FEE_PCT + SLIPPAGE_PCT

    for i in range(min_lookback, len(df)):
        window = df.iloc[: i + 1]
        current_bar = df.iloc[i]
        regime = regime_detector.detect(window)

        for strategy in strategies:
            pos = current_positions[strategy.name]

            if pos is None:
                if strategy.should_run(regime):
                    signal = strategy.generate_signal(symbol, window, regime)
                    if signal:
                        # Add Entry Friction
                        effective_entry = signal.entry_price * (
                            1 + TOTAL_FRICTION if signal.side == Side.BUY else 1 - TOTAL_FRICTION
                        )
                        current_positions[strategy.name] = {
                            "entry_price": effective_entry,
                            "stop_loss": signal.stop_loss,
                            "take_profit": signal.take_profit,
                            "side": signal.side,
                            "entry_time": window.index[-1],
                            "regime": regime.value,
                            "reason": signal.reason,
                            "strategy": signal.strategy_name,
                        }
            else:
                # Check for exit on current bar
                high = float(current_bar["high"])
                low = float(current_bar["low"])
                exit_price = None

                # CONSERVATIVE WICK LOGIC:
                # If a candle hits both SL and TP, assume SL hit first.
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
                    # Apply Exit Friction
                    effective_exit = exit_price * (
                        1 - TOTAL_FRICTION if pos["side"] == Side.BUY else 1 + TOTAL_FRICTION
                    )

                    # Calculate P&L
                    if pos["side"] == Side.BUY:
                        pnl_pct = (
                            (effective_exit - pos["entry_price"]) / pos["entry_price"]
                        ) * 100
                    else:
                        pnl_pct = (
                            (pos["entry_price"] - effective_exit) / pos["entry_price"]
                        ) * 100

                    trades.append(
                        {
                            "timestamp": pos["entry_time"],
                            "exit_time": window.index[-1],
                            "strategy": pos["strategy"],
                            "side": pos["side"].value,
                            "entry": pos["entry_price"],
                            "exit": effective_exit,
                            "pnl_pct": pnl_pct,
                            "regime": pos["regime"],
                            "reason": pos["reason"],
                        }
                    )
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

    # 1. Equity Curve (Compounding)
    initial_capital = 10000.0  # Normalized starting capital
    equity_curve = [initial_capital]
    for pnl in trades_df["pnl_pct"]:
        next_val = equity_curve[-1] * (1 + pnl / 100)
        equity_curve.append(next_val)

    trades_df["equity"] = equity_curve[1:]
    final_equity = equity_curve[-1]
    total_net_roi = ((final_equity / initial_capital) - 1) * 100

    # 2. Max Drawdown (MDD)
    equity_series = pd.Series(equity_curve)
    rolling_max = equity_series.cummax()
    drawdowns = (equity_series - rolling_max) / rolling_max
    max_drawdown = drawdowns.min() * 100

    # 3. Report Generation
    logger.info(f"\n{'═' * 60}")
    logger.info(f"BACKTEST RESULTS: {symbol}")
    logger.info(f"{'═' * 60}")
    logger.info(f"Period: {df.index[0]} to {df.index[-1]}")
    logger.info(f"Total Trades: {len(trades_df)}")

    winning = trades_df[trades_df["pnl_pct"] > 0]
    losing = trades_df[trades_df["pnl_pct"] < 0]

    logger.info(f"Winning: {len(winning)} | Losing: {len(losing)}")
    logger.info(f"Win Rate: {len(winning) / len(trades_df) * 100:.1f}%")
    logger.info(
        f"Avg Win: {winning['pnl_pct'].mean():.2f}%"
        if len(winning) > 0
        else "Avg Win: N/A"
    )
    logger.info(
        f"Avg Loss: {losing['pnl_pct'].mean():.2f}%"
        if len(losing) > 0
        else "Avg Loss: N/A"
    )

    logger.info(f"{'-' * 60}")
    logger.info(f"GROSS Return (sum): {trades_df['pnl_pct'].sum():.2f}%")
    logger.info(f"NET Return (compounded): {total_net_roi:.2f}%")
    logger.info(f"Max Drawdown: {max_drawdown:.2f}%")
    logger.info(f"Final Equity: ${final_equity:,.2f}")
    logger.info(f"{'═' * 60}")

    # Per-strategy breakdown
    logger.info(f"\nPer-Strategy Breakdown (Net ROI %):")
    for strat_name, group in trades_df.groupby("strategy"):
        w = group[group["pnl_pct"] > 0]
        # Calculate strat-specific net ROI
        strat_final = 1.0
        for p in group["pnl_pct"]:
            strat_final *= 1 + p / 100
        strat_roi = (strat_final - 1) * 100

        logger.info(
            f"  {strat_name:<15}: {len(group):>3} trades | "
            f"Win Rate: {len(w) / len(group) * 100:>2.0f}% | "
            f"Net ROI: {strat_roi:>7.2f}%"
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
    parser.add_argument(
        "--symbol",
        default=None,
        help="Symbol to backtest (defaults to BTC/USDT:USDT, or SPY when using Alpaca equities)",
    )
    parser.add_argument("--days", type=int, default=30, help="Number of days to backtest")
    parser.add_argument("--strategy", default="all", help="Strategy name or 'all'")
    parser.add_argument("--timeframe", default="5Min", help="Candle timeframe")
    args = parser.parse_args()

    import yaml
    config_path = Path(__file__).parent.parent / "config" / "settings.yaml"
    try:
        with open(config_path, "r") as f:
            settings = yaml.safe_load(f)
        asset_class = settings.get("trading", {}).get("asset_class", "crypto").lower()
    except Exception:
        asset_class = "crypto"
        
    if args.symbol is None:
        args.symbol = "BTC/USDT:USDT" if asset_class == "crypto" else "SPY"

    run_backtest(args.symbol, args.days, args.strategy, args.timeframe)

if __name__ == "__main__":
    main()
