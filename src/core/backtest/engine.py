"""
Backtest Engine.
Core simulation logic for executing strategies against historical OHLCV data.
"""

from __future__ import annotations

from typing import List

import pandas as pd
from loguru import logger

from src.core.backtest.models import BacktestResult, BacktestTrade
from src.core.models import Side
from src.intelligence.regime_detector import RegimeDetector


class BacktestEngine:
    """
    Modular simulation engine with realistic friction modeling.
    """

    def __init__(
        self,
        fee_pct: float = 0.055,
        slippage_pct: float = 0.1,
        initial_capital: float = 10000.0,
    ):
        self.fee_pct = fee_pct / 100
        self.slippage_pct = slippage_pct / 100
        self.total_friction = self.fee_pct + self.slippage_pct
        self.initial_capital = initial_capital
        self.regime_detector = RegimeDetector()

    def run(
        self,
        symbol: str,
        df: pd.DataFrame,
        strategy,
        min_lookback: int = 50,
    ) -> BacktestResult:
        """
        Execute a walk-forward simulation for a single strategy.
        """
        logger.debug(f"Starting simulation core for {strategy.name} on {symbol}")

        trades: List[BacktestTrade] = []
        current_pos = None

        for i in range(min_lookback, len(df)):
            window = df.iloc[: i + 1]
            current_bar = df.iloc[i]
            regime = self.regime_detector.detect(window)

            if current_pos is None:
                # 1. Entry Logic
                if strategy.should_run(regime):
                    signal = strategy.generate_signal(symbol, window, regime)
                    if signal:
                        # Apply Entry Friction
                        effective_entry = signal.entry_price * (
                            1 + self.total_friction
                            if signal.side == Side.BUY
                            else 1 - self.total_friction
                        )
                        current_pos = {
                            "entry_price": effective_entry,
                            "stop_loss": signal.stop_loss,
                            "take_profit": signal.take_profit,
                            "side": signal.side,
                            "entry_time": window.index[-1],
                            "regime": regime.value,
                            "reason": signal.reason,
                        }
            else:
                # 2. Exit Logic (Management)
                high = float(current_bar["high"])
                low = float(current_bar["low"])
                exit_price = None

                # Conservative Wick Logic
                if current_pos["side"] == Side.BUY:
                    if low <= current_pos["stop_loss"]:
                        exit_price = current_pos["stop_loss"]
                    elif (
                        current_pos["take_profit"]
                        and high >= current_pos["take_profit"]
                    ):
                        exit_price = current_pos["take_profit"]
                else:
                    if high >= current_pos["stop_loss"]:
                        exit_price = current_pos["stop_loss"]
                    elif (
                        current_pos["take_profit"] and low <= current_pos["take_profit"]
                    ):
                        exit_price = current_pos["take_profit"]

                if exit_price is not None:
                    # Apply Exit Friction
                    effective_exit = exit_price * (
                        1 - self.total_friction
                        if current_pos["side"] == Side.BUY
                        else 1 + self.total_friction
                    )

                    # Create Trade Record
                    trades.append(
                        self._create_trade_record(
                            symbol,
                            current_pos,
                            effective_exit,
                            window.index[-1],
                            strategy.name,
                        )
                    )
                    current_pos = None

        # Handle EOD force-close
        if current_pos:
            last_close = float(df.iloc[-1]["close"])
            trades.append(
                self._create_trade_record(
                    symbol,
                    current_pos,
                    last_close,
                    df.index[-1],
                    strategy.name,
                    "EOD Close",
                )
            )

        return self._summarize(symbol, df, trades)

    def _create_trade_record(
        self, symbol, pos, exit_price, exit_time, strategy_name, reason_suffix=""
    ) -> BacktestTrade:
        if pos["side"] == Side.BUY:
            pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
        else:
            pnl_pct = ((pos["entry_price"] - exit_price) / pos["entry_price"]) * 100

        return BacktestTrade(
            symbol=symbol,
            side=pos["side"].value,
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            entry_time=pos["entry_time"],
            exit_time=exit_time,
            pnl_pct=pnl_pct,
            regime=pos["regime"],
            strategy=strategy_name,
            reason=pos["reason"] + reason_suffix,
        )

    def _summarize(self, symbol, df, trades: List[BacktestTrade]) -> BacktestResult:
        """Calculate aggregate performance metrics."""
        result = BacktestResult(
            symbol=symbol,
            period_start=df.index[0],
            period_end=df.index[-1],
            trades=trades,
            initial_capital=self.initial_capital,
        )

        if not trades:
            return result

        # ROI & Equity
        equity = self.initial_capital
        gross_pnl_sum = 0.0
        for t in trades:
            equity *= 1 + t.pnl_pct / 100
            gross_pnl_sum += t.pnl_pct

        result.final_equity = equity
        result.net_roi_pct = ((equity / self.initial_capital) - 1) * 100
        result.gross_roi_pct = gross_pnl_sum

        # Winning stats
        winners = [t for t in trades if t.pnl_pct > 0]
        losers = [t for t in trades if t.pnl_pct < 0]
        result.win_rate_pct = (len(winners) / len(trades)) * 100
        result.avg_win_pct = sum(t.pnl_pct for t in winners) / max(1, len(winners))
        result.avg_loss_pct = sum(t.pnl_pct for t in losers) / max(1, len(losers))

        # Drawdown
        equity_curve = [self.initial_capital]
        current_eq = self.initial_capital
        for t in trades:
            current_eq *= 1 + t.pnl_pct / 100
            equity_curve.append(current_eq)

        eq_series = pd.Series(equity_curve)
        rolling_max = eq_series.cummax()
        drawdowns = (eq_series - rolling_max) / rolling_max
        result.max_drawdown_pct = drawdowns.min() * 100

        return result
