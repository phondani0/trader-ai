"""
Backtest Engine.
Core simulation logic for executing strategies against historical OHLCV data.
"""

from __future__ import annotations

from datetime import datetime
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
        max_drawdown_pct: float = 20.0,
    ):
        self.fee_pct = fee_pct / 100
        self.slippage_pct = slippage_pct / 100
        self.total_friction = self.fee_pct + self.slippage_pct
        self.initial_capital = initial_capital
        self.max_drawdown_pct = max_drawdown_pct
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
        return self.run_portfolio(symbol, df, [strategy], min_lookback=min_lookback)

    def run_portfolio(
        self,
        symbol: str,
        df: pd.DataFrame,
        strategies: list,
        min_lookback: int = 50,
    ) -> BacktestResult:
        """
        Execute a walk-forward simulation for one or more strategies.
        """
        strategy_label = ", ".join(strategy.name for strategy in strategies)
        logger.debug(f"Starting simulation core for {strategy_label} on {symbol}")

        trades: List[BacktestTrade] = []
        current_positions = {strategy.name: None for strategy in strategies}
        running_equity = self.initial_capital
        peak_equity = self.initial_capital
        killed_by_drawdown = False
        kill_reason = ""

        for i in range(min_lookback, len(df)):
            window = df.iloc[: i + 1]
            current_bar = df.iloc[i]
            regime = self.regime_detector.detect(window)

            for strategy in strategies:
                current_pos = current_positions[strategy.name]

                if current_pos is None:
                    if killed_by_drawdown:
                        continue

                    if strategy.should_run(regime):
                        signal = strategy.generate_signal(symbol, window, regime)
                        if signal:
                            effective_entry = signal.entry_price * (
                                1 + self.total_friction
                                if signal.side == Side.BUY
                                else 1 - self.total_friction
                            )
                            current_positions[strategy.name] = {
                                "entry_price": effective_entry,
                                "stop_loss": signal.stop_loss,
                                "take_profit": signal.take_profit,
                                "side": signal.side,
                                "entry_time": window.index[-1],
                                "regime": regime.value,
                                "reason": signal.reason,
                                "strategy": strategy.name,
                            }
                else:
                    exit_price = self._resolve_exit_price(current_pos, current_bar)
                    if exit_price is None:
                        continue

                    effective_exit = exit_price * (
                        1 - self.total_friction
                        if current_pos["side"] == Side.BUY
                        else 1 + self.total_friction
                    )
                    trade = self._create_trade_record(
                        symbol,
                        current_pos,
                        effective_exit,
                        window.index[-1],
                        strategy.name,
                    )
                    trades.append(trade)
                    current_positions[strategy.name] = None

                    running_equity *= 1 + trade.pnl_pct / 100
                    peak_equity = max(peak_equity, running_equity)
                    drawdown_pct = ((running_equity - peak_equity) / peak_equity) * 100
                    if drawdown_pct <= -self.max_drawdown_pct:
                        killed_by_drawdown = True
                        kill_reason = (
                            f"max drawdown {abs(drawdown_pct):.2f}% exceeded "
                            f"{self.max_drawdown_pct:.1f}%"
                        )
                        logger.warning(
                            f"Backtest drawdown kill-switch triggered for {strategy.name} on {symbol}: {kill_reason}"
                        )
                        break

            if killed_by_drawdown:
                break

        last_close = float(df.iloc[-1]["close"])
        last_time = df.index[-1]
        for strategy_name, current_pos in current_positions.items():
            if current_pos is None:
                continue
            trades.append(
                self._create_trade_record(
                    symbol,
                    current_pos,
                    last_close,
                    last_time,
                    strategy_name,
                    "EOD Close",
                )
            )

        return self.summarize(
            symbol=symbol,
            period_start=df.index[0],
            period_end=df.index[-1],
            trades=trades,
            killed_by_drawdown=killed_by_drawdown,
            kill_reason=kill_reason,
        )

    def summarize(
        self,
        symbol: str,
        period_start: datetime,
        period_end: datetime,
        trades: List[BacktestTrade],
        *,
        killed_by_drawdown: bool = False,
        kill_reason: str = "",
    ) -> BacktestResult:
        """
        Calculate aggregate performance metrics for a set of simulated trades.
        """
        result = BacktestResult(
            symbol=symbol,
            period_start=period_start,
            period_end=period_end,
            trades=trades,
            initial_capital=self.initial_capital,
            killed_by_drawdown=killed_by_drawdown,
            kill_switch_reason=kill_reason,
        )

        if not trades:
            return result

        equity = self.initial_capital
        gross_pnl_sum = 0.0
        equity_curve = [self.initial_capital]
        winners: list[float] = []
        losers: list[float] = []

        for trade in trades:
            equity *= 1 + trade.pnl_pct / 100
            gross_pnl_sum += trade.pnl_pct
            equity_curve.append(equity)
            if trade.pnl_pct > 0:
                winners.append(trade.pnl_pct)
            elif trade.pnl_pct < 0:
                losers.append(abs(trade.pnl_pct))

        result.final_equity = equity
        result.net_roi_pct = ((equity / self.initial_capital) - 1) * 100
        result.gross_roi_pct = gross_pnl_sum
        result.win_rate_pct = (len(winners) / len(trades)) * 100
        result.avg_win_pct = sum(winners) / max(1, len(winners))
        result.avg_loss_pct = -(sum(losers) / max(1, len(losers)))
        result.expectancy_pct = sum(trade.pnl_pct for trade in trades) / len(trades)

        if losers:
            result.profit_factor = sum(winners) / sum(losers) if winners else 0.0
        elif winners:
            result.profit_factor = float("inf")

        eq_series = pd.Series(equity_curve)
        rolling_max = eq_series.cummax()
        drawdowns = (eq_series - rolling_max) / rolling_max
        result.max_drawdown_pct = drawdowns.min() * 100
        result.regime_breakdown = self._build_breakdown(trades, key="regime")
        result.strategy_breakdown = self._build_breakdown(trades, key="strategy")

        return result

    def _resolve_exit_price(
        self, current_pos: dict, current_bar: pd.Series
    ) -> float | None:
        """
        Resolve an exit using conservative wick logic.
        """
        high = float(current_bar["high"])
        low = float(current_bar["low"])

        if current_pos["side"] == Side.BUY:
            if low <= current_pos["stop_loss"]:
                return current_pos["stop_loss"]
            if current_pos["take_profit"] and high >= current_pos["take_profit"]:
                return current_pos["take_profit"]
            return None

        if high >= current_pos["stop_loss"]:
            return current_pos["stop_loss"]
        if current_pos["take_profit"] and low <= current_pos["take_profit"]:
            return current_pos["take_profit"]
        return None

    def _create_trade_record(
        self,
        symbol: str,
        pos: dict,
        exit_price: float,
        exit_time,
        strategy_name: str,
        reason_suffix: str = "",
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

    @staticmethod
    def _build_breakdown(trades: List[BacktestTrade], key: str) -> dict:
        breakdown: dict = {}
        grouped: dict[str, list[BacktestTrade]] = {}
        for trade in trades:
            grouped.setdefault(getattr(trade, key), []).append(trade)

        for group_key, group_trades in grouped.items():
            winners = [trade.pnl_pct for trade in group_trades if trade.pnl_pct > 0]
            net_factor = 1.0
            for trade in group_trades:
                net_factor *= 1 + trade.pnl_pct / 100
            breakdown[group_key] = {
                "trades": len(group_trades),
                "win_rate_pct": (len(winners) / len(group_trades)) * 100,
                "net_roi_pct": (net_factor - 1) * 100,
                "expectancy_pct": sum(trade.pnl_pct for trade in group_trades)
                / len(group_trades),
            }

        return breakdown
