"""
Backtest Engine.
Core simulation logic for executing strategies against historical OHLCV data.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

import numpy as np

import pandas as pd
from loguru import logger

from src.core.backtest.models import BacktestResult, BacktestTrade
from src.core.backtest.monte_carlo import MonteCarloAnalyzer
from src.core.models import Side, MarketBias, StrategyType
from src.core.timeframes import canonical_timeframe, timeframe_to_minutes
from src.intelligence.regime_detector import RegimeDetector
from src.intelligence.signal_aggregator import SignalAggregator


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
        Execute a walk-forward simulation for one or more independent strategies.

        Each strategy may hold its own position on the same symbol. This mode is
        useful for research, but not for simulating the live one-best-signal flow.
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
            regime_state = self.regime_detector.detect(window)
            regime = regime_state.regime
            bias = regime_state.bias

            for strategy in strategies:
                current_pos = current_positions[strategy.name]

                if current_pos is None:
                    if killed_by_drawdown:
                        continue

                    if strategy.should_run(regime):
                        signal = strategy.generate_signal(symbol, window, regime)
                        if signal:
                            if strategy.strategy_type == StrategyType.TREND_FOLLOWING:
                                if bias == MarketBias.BULLISH and signal.side == Side.SELL:
                                    logger.info(f"🚫 Bias Gate: REJECTED {strategy.name} SELL signal (Macro Bias: BULLISH)")
                                    continue
                                if bias == MarketBias.BEARISH and signal.side == Side.BUY:
                                    logger.info(f"🚫 Bias Gate: REJECTED {strategy.name} BUY signal (Macro Bias: BEARISH)")
                                    continue

                            current_positions[strategy.name] = {
                                "entry_price": self._apply_entry_friction(
                                    signal.side,
                                    signal.entry_price,
                                ),
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

                    trade = self._create_trade_record(
                        symbol,
                        current_pos,
                        self._apply_exit_friction(current_pos["side"], exit_price),
                        window.index[-1],
                        strategy.name,
                    )
                    trades.append(trade)
                    current_positions[strategy.name] = None

                    running_equity, peak_equity, drawdown_pct = self._update_equity(
                        running_equity,
                        peak_equity,
                        trade.pnl_pct,
                    )
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
                    self._apply_exit_friction(current_pos["side"], last_close),
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

    def run_selector(
        self,
        symbol: str,
        df: pd.DataFrame | dict[str, pd.DataFrame],
        strategies: list,
        min_lookback: int = 50,
    ) -> BacktestResult:
        """
        Execute a live-style walk-forward simulation.

        This mode keeps a single shared position for the symbol and lets the
        signal aggregator pick the best available signal at each bar.
        """
        strategy_label = ", ".join(strategy.name for strategy in strategies)
        logger.debug(f"Starting selector simulation for {strategy_label} on {symbol}")

        if isinstance(df, dict):
            return self._run_selector_multitimeframe(
                symbol,
                df,
                strategies,
                min_lookback=min_lookback,
            )

        trades: List[BacktestTrade] = []
        current_position: dict | None = None
        running_equity = self.initial_capital
        peak_equity = self.initial_capital
        killed_by_drawdown = False
        kill_reason = ""
        aggregator = SignalAggregator(
            strategies=strategies,
            regime_detector=self.regime_detector,
        )
        market_profile_refresh_bars = 12
        cached_market_profile: str | None = None
        cached_market_bias: str | None = None

        from tqdm import tqdm
        
        for i in tqdm(
            range(min_lookback, len(df)),
            desc=f"Simulating {strategy_label[:20]}",
            leave=False,
            dynamic_ncols=True,
            unit="bar"
        ):
            window = df.iloc[: i + 1]
            current_bar = df.iloc[i]

            if current_position is not None:
                exit_price = self._resolve_exit_price(current_position, current_bar)
                if exit_price is not None:
                    trade = self._create_trade_record(
                        symbol,
                        current_position,
                        self._apply_exit_friction(current_position["side"], exit_price),
                        window.index[-1],
                        current_position["strategy"],
                    )
                    trades.append(trade)
                    current_position = None

                    running_equity, peak_equity, drawdown_pct = self._update_equity(
                        running_equity,
                        peak_equity,
                        trade.pnl_pct,
                    )
                    if drawdown_pct <= -self.max_drawdown_pct:
                        killed_by_drawdown = True
                        kill_reason = (
                            f"max drawdown {abs(drawdown_pct):.2f}% exceeded "
                            f"{self.max_drawdown_pct:.1f}%"
                        )
                        logger.warning(
                            f"Backtest drawdown kill-switch triggered for selector on {symbol}: {kill_reason}"
                        )
                        break

            if current_position is not None or killed_by_drawdown:
                continue

            if cached_market_profile is None or (i - min_lookback) % market_profile_refresh_bars == 0:
                profile_window = aggregator.market_profile_window(window)
                market_context = self.regime_detector.profile_window(profile_window)
                cached_market_profile = market_context.get("detected_market_profile")
                cached_market_bias = market_context.get("market_bias")

            signals = aggregator.generate_signals(
                {symbol: window},
                log_signals=False,
                market_profiles={symbol: cached_market_profile} if cached_market_profile else None,
                market_biases={symbol: cached_market_bias} if cached_market_bias else None,
            )
            if not signals:
                continue

            signal = signals[0]
            current_position = {
                "entry_price": self._apply_entry_friction(
                    signal.side,
                    signal.entry_price,
                ),
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "side": signal.side,
                "entry_time": window.index[-1],
                "regime": signal.regime.value,
                "reason": signal.reason,
                "strategy": signal.strategy_name,
            }

        if current_position is not None:
            last_close = float(df.iloc[-1]["close"])
            last_time = df.index[-1]
            trades.append(
                self._create_trade_record(
                    symbol,
                    current_position,
                    self._apply_exit_friction(current_position["side"], last_close),
                    last_time,
                    current_position["strategy"],
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

    def _run_selector_multitimeframe(
        self,
        symbol: str,
        frames: dict[str, pd.DataFrame],
        strategies: list,
        *,
        min_lookback: int,
    ) -> BacktestResult:
        active_frames = {
            canonical_timeframe(timeframe): frame.sort_index()
            for timeframe, frame in frames.items()
            if isinstance(frame, pd.DataFrame) and not frame.empty
        }
        if not active_frames:
            now = datetime.utcnow()
            return self.summarize(
                symbol=symbol,
                period_start=now,
                period_end=now,
                trades=[],
            )

        execution_timeframe = min(
            active_frames,
            key=lambda timeframe: timeframe_to_minutes(timeframe),
        )
        execution_frame = active_frames[execution_timeframe]
        timeline = sorted({timestamp for frame in active_frames.values() for timestamp in frame.index})

        trades: List[BacktestTrade] = []
        current_position: dict | None = None
        running_equity = self.initial_capital
        peak_equity = self.initial_capital
        killed_by_drawdown = False
        kill_reason = ""
        aggregator = SignalAggregator(
            strategies=strategies,
            regime_detector=self.regime_detector,
        )

        market_profile_refresh_bars = 12
        cached_market_profile: str | None = None
        cached_market_bias: str | None = None

        from tqdm import tqdm

        bar_count = 0
        for timestamp in tqdm(
            timeline,
            desc=f"Simulating {strategy.name[:20] if strategies and (strategy := strategies[0]) else 'Multi-TF'}",
            leave=False,
            dynamic_ncols=True,
            unit="bar"
        ):
            execution_window = execution_frame.loc[execution_frame.index <= timestamp]
            if execution_window.empty:
                continue
            current_bar = execution_window.iloc[-1]

            if current_position is not None:
                exit_price = self._resolve_exit_price(current_position, current_bar)
                if exit_price is not None:
                    trade = self._create_trade_record(
                        symbol,
                        current_position,
                        self._apply_exit_friction(current_position["side"], exit_price),
                        timestamp,
                        current_position["strategy"],
                    )
                    trades.append(trade)
                    current_position = None

                    running_equity, peak_equity, drawdown_pct = self._update_equity(
                        running_equity,
                        peak_equity,
                        trade.pnl_pct,
                    )
                    if drawdown_pct <= -self.max_drawdown_pct:
                        killed_by_drawdown = True
                        kill_reason = (
                            f"max drawdown {abs(drawdown_pct):.2f}% exceeded "
                            f"{self.max_drawdown_pct:.1f}%"
                        )
                        logger.warning(
                            f"Backtest drawdown kill-switch triggered for selector on {symbol}: {kill_reason}"
                        )
                        break

            if current_position is not None or killed_by_drawdown:
                continue

            fresh_windows: dict[str, pd.DataFrame] = {}
            for timeframe, frame in active_frames.items():
                if timestamp not in frame.index:
                    continue
                # Instead of a heavy .loc each time on a huge df, we might just slice. But loc is fine for now if cached.
                window = frame.loc[:timestamp]
                if len(window) < min_lookback:
                    continue
                fresh_windows[timeframe] = window

            if not fresh_windows:
                continue

            # Update cached market profile every market_profile_refresh_bars of the execution timeframe
            if cached_market_profile is None or bar_count % market_profile_refresh_bars == 0:
                profile_window = aggregator.market_profile_window(fresh_windows)
                market_context = self.regime_detector.profile_window(profile_window)
                cached_market_profile = market_context.get("detected_market_profile")
                cached_market_bias = market_context.get("market_bias")
            
            bar_count += 1

            signals = aggregator.generate_signals(
                {symbol: fresh_windows},
                log_signals=False,
                market_profiles={symbol: cached_market_profile} if cached_market_profile else None,
                market_biases={symbol: cached_market_bias} if cached_market_bias else None,
            )
            if not signals:
                continue

            signal = signals[0]
            current_position = {
                "entry_price": self._apply_entry_friction(
                    signal.side,
                    signal.entry_price,
                ),
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "side": signal.side,
                "entry_time": timestamp,
                "regime": signal.regime.value,
                "reason": signal.reason,
                "strategy": signal.strategy_name,
            }

        if current_position is not None:
            last_close = float(execution_frame.iloc[-1]["close"])
            last_time = execution_frame.index[-1]
            trades.append(
                self._create_trade_record(
                    symbol,
                    current_position,
                    self._apply_exit_friction(current_position["side"], last_close),
                    last_time,
                    current_position["strategy"],
                    "EOD Close",
                )
            )

        return self.summarize(
            symbol=symbol,
            period_start=min(frame.index[0] for frame in active_frames.values()),
            period_end=max(frame.index[-1] for frame in active_frames.values()),
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
        run_monte_carlo: bool = False,
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
            result.final_equity = self.initial_capital
            result.equity_curve = [self.initial_capital]
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

        result.equity_curve = equity_curve
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

        timestamps = [period_start] + [trade.exit_time for trade in trades]
        eq_series = pd.Series(equity_curve, index=timestamps)
        rolling_max = eq_series.cummax()
        drawdowns = (eq_series - rolling_max) / rolling_max
        result.max_drawdown_pct = drawdowns.min() * 100
        
        underwater = drawdowns < 0
        if underwater.any():
            peaks = eq_series[drawdowns == 0].index
            if len(peaks) > 1:
                durations = (peaks[1:] - peaks[:-1]).max().total_seconds() / 86400.0
                current_underwater = (timestamps[-1] - peaks[-1]).total_seconds() / 86400.0
                result.max_drawdown_duration_days = max(durations, current_underwater)
            elif len(peaks) == 1:
                result.max_drawdown_duration_days = (timestamps[-1] - peaks[0]).total_seconds() / 86400.0
        else:
            result.max_drawdown_duration_days = 0.0
        result.regime_breakdown = self._build_breakdown(trades, key="regime")
        result.strategy_breakdown = self._build_breakdown(trades, key="strategy")

        # Risk-adjusted metrics (Sharpe, Sortino, Calmar)
        result.sharpe_ratio, result.sortino_ratio, result.calmar_ratio = (
            self._compute_risk_metrics(equity_curve, period_start, period_end)
        )
        if result.sharpe_ratio != 0.0:
            total_return = result.net_roi_pct / 100
            days = max((period_end - period_start).days, 1)
            ann_factor = 365 / days
            result.annualized_return_pct = ((1 + total_return) ** ann_factor - 1) * 100
            returns = np.diff(equity_curve) / equity_curve[:-1]
            result.annualized_volatility_pct = (
                float(np.std(returns, ddof=1) * np.sqrt(365 / max(days / len(returns), 1)) * 100)
                if len(returns) > 1
                else 0.0
            )

        if run_monte_carlo and len(trades) >= 5:
            trade_returns = [trade.pnl_pct for trade in trades]
            mc_analyzer = MonteCarloAnalyzer(n_simulations=1000, confidence=0.95)
            mc_results = mc_analyzer.analyze_drawdowns(trade_returns)
            result.mc_expected_mdd_pct = mc_results["expected_max_dd_pct"]
            result.mc_worst_95_mdd_pct = mc_results["worst_95pct_mdd"]

        return result

    @staticmethod
    def _compute_risk_metrics(
        equity_curve: list[float],
        period_start: datetime,
        period_end: datetime,
        risk_free_rate: float = 0.04,
    ) -> tuple[float, float, float]:
        """
        Compute Sharpe, Sortino, and Calmar ratios from an equity curve.

        Uses crypto's 365-day trading year for annualization.
        """
        if len(equity_curve) < 3:
            return 0.0, 0.0, 0.0

        eq = np.array(equity_curve, dtype=float)
        returns = np.diff(eq) / eq[:-1]

        if len(returns) < 2 or np.std(returns, ddof=1) == 0:
            return 0.0, 0.0, 0.0

        days = max((period_end - period_start).days, 1)
        trades_per_year = len(returns) * (365 / days)

        mean_return = float(np.mean(returns))
        std_return = float(np.std(returns, ddof=1))

        # Annualized return and volatility
        ann_return = (1 + mean_return) ** trades_per_year - 1
        ann_vol = std_return * np.sqrt(trades_per_year)

        # Sharpe
        sharpe = (ann_return - risk_free_rate) / ann_vol if ann_vol > 0 else 0.0

        # Sortino (downside deviation only)
        downside = returns[returns < 0]
        if len(downside) > 0:
            downside_std = float(np.std(downside, ddof=1))
            ann_downside_vol = downside_std * np.sqrt(trades_per_year)
            sortino = (
                (ann_return - risk_free_rate) / ann_downside_vol
                if ann_downside_vol > 0
                else 0.0
            )
        else:
            sortino = float("inf") if ann_return > risk_free_rate else 0.0

        # Calmar (annualized return / max drawdown)
        rolling_max = np.maximum.accumulate(eq)
        drawdowns = (eq - rolling_max) / rolling_max
        max_dd = float(np.min(drawdowns))
        calmar = ann_return / abs(max_dd) if max_dd != 0 else 0.0

        return round(sharpe, 3), round(sortino, 3), round(calmar, 3)

    def _resolve_exit_price(
        self,
        current_pos: dict,
        current_bar: pd.Series,
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

    def _apply_entry_friction(self, side: Side, entry_price: float) -> float:
        if side == Side.BUY:
            return entry_price * (1 + self.total_friction)
        return entry_price * (1 - self.total_friction)

    def _apply_exit_friction(self, side: Side, exit_price: float) -> float:
        if side == Side.BUY:
            return exit_price * (1 - self.total_friction)
        return exit_price * (1 + self.total_friction)

    @staticmethod
    def _update_equity(
        running_equity: float,
        peak_equity: float,
        pnl_pct: float,
    ) -> tuple[float, float, float]:
        running_equity *= 1 + pnl_pct / 100
        peak_equity = max(peak_equity, running_equity)
        drawdown_pct = ((running_equity - peak_equity) / peak_equity) * 100
        return running_equity, peak_equity, drawdown_pct
