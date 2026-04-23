"""
Walk-Forward Analysis (WFA) Optimizer.

Implements rolling Train/Test validation to optimize trading strategy
parameters dynamically and prevent curve-fitting.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable, Dict, List, Any

import pandas as pd
from loguru import logger

from src.core.backtest.engine import BacktestEngine
from src.core.backtest.models import BacktestResult


@dataclass
class WFASplit:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train_data: pd.DataFrame
    test_data: pd.DataFrame


class WalkForwardOptimizer:
    def __init__(
        self,
        engine: BacktestEngine,
        train_days: int = 90,
        test_days: int = 30,
        anchored: bool = False,
    ):
        """
        Args:
            engine: The BacktestEngine instance to use for simulation.
            train_days: Number of days in the training window (in-sample).
            test_days: Number of days in the testing window (out-of-sample).
            anchored: If True, the training window always starts from the beginning of the dataset.
        """
        self.engine = engine
        self.train_days = train_days
        self.test_days = test_days
        self.anchored = anchored

    def generate_splits(self, df: pd.DataFrame) -> List[WFASplit]:
        """Generate rolling Train/Test splits from the provided dataframe."""
        splits = []
        if df.empty:
            return splits

        start_time = df.index[0]
        end_time = df.index[-1]
        
        current_train_start = start_time
        
        while True:
            train_end = current_train_start + pd.Timedelta(days=self.train_days)
            test_start = train_end
            test_end = test_start + pd.Timedelta(days=self.test_days)
            
            if test_end > end_time:
                # If the test window exceeds the data, we stop.
                # Optional: We could allow a partial final test window.
                break
                
            train_data = df.loc[current_train_start:train_end]
            test_data = df.loc[test_start:test_end]
            
            if not train_data.empty and not test_data.empty:
                splits.append(
                    WFASplit(
                        train_start=current_train_start,
                        train_end=train_end,
                        test_start=test_start,
                        test_end=test_end,
                        train_data=train_data,
                        test_data=test_data,
                    )
                )
            
            if self.anchored:
                # Training window grows, testing shifts forward
                current_train_start = start_time
            else:
                # Rolling window: both shift forward by test_days
                current_train_start += pd.Timedelta(days=self.test_days)
                
        return splits

    def grid_search(
        self,
        symbol: str,
        df: pd.DataFrame,
        strategy_factory: Callable[[dict], Any],
        base_params: dict,
        param_grid: dict[str, list],
        optimize_metric: str = "sortino_ratio",
    ) -> tuple[dict, BacktestResult]:
        """
        Run a grid search to find the optimal parameter combination for a single window.
        
        Args:
            strategy_factory: A function that takes a dict of params and returns a Strategy instance.
            base_params: The base strategy parameters.
            param_grid: The grid of parameters to search over.
            optimize_metric: The attribute of BacktestResult to maximize.
            
        Returns:
            Tuple of (best_params, best_result).
        """
        best_params = base_params.copy()
        best_metric_val = -float("inf")
        best_result = None

        if not param_grid:
            strategy = strategy_factory(base_params)
            result = self.engine.run(symbol, df, strategy)
            return base_params, result

        keys = list(param_grid.keys())
        values = list(param_grid.values())
        
        for combination in itertools.product(*values):
            current_params = base_params.copy()
            for k, v in zip(keys, combination):
                current_params[k] = v
                
            strategy = strategy_factory(current_params)
            result = self.engine.run(symbol, df, strategy)
            
            metric_val = getattr(result, optimize_metric, -float("inf"))
            
            if metric_val > best_metric_val:
                best_metric_val = metric_val
                best_params = current_params
                best_result = result
                
        if best_result is None:
            # Fallback if all combinations fail or produce no trades
            strategy = strategy_factory(base_params)
            best_result = self.engine.run(symbol, df, strategy)
            
        return best_params, best_result

    def run_walk_forward(
        self,
        symbol: str,
        df: pd.DataFrame,
        strategy_factory: Callable[[dict], Any],
        base_params: dict,
        param_grid: dict[str, list],
        optimize_metric: str = "sortino_ratio",
    ) -> dict[str, Any]:
        """
        Execute the full Walk-Forward Analysis over rolling windows.
        
        Returns:
            Dict containing the out-of-sample (OOS) performance, parameter history, and splits.
        """
        splits = self.generate_splits(df)
        if not splits:
            logger.warning("Not enough data to generate any Train/Test splits.")
            return {}

        logger.info(f"Generated {len(splits)} rolling splits (Train: {self.train_days}d, Test: {self.test_days}d)")

        oos_trades = []
        param_history = []
        in_sample_results = []
        out_of_sample_results = []

        for i, split in enumerate(splits):
            logger.info(f"WFA Split {i+1}/{len(splits)} | Train: {split.train_start.date()} to {split.train_end.date()}")
            
            # Step 1: Optimize on Train data (In-Sample)
            best_params, is_result = self.grid_search(
                symbol,
                split.train_data,
                strategy_factory,
                base_params,
                param_grid,
                optimize_metric,
            )
            
            is_metric = getattr(is_result, optimize_metric, 0.0)
            logger.info(f"  └─ Best Params: {best_params} -> Train {optimize_metric}: {is_metric:.2f}")
            
            # Step 2: Test on Test data (Out-of-Sample)
            test_strategy = strategy_factory(best_params)
            oos_result = self.engine.run(symbol, split.test_data, test_strategy)
            
            oos_metric = getattr(oos_result, optimize_metric, 0.0)
            
            # Compute Degradation (if IS metric is positive)
            degradation = 0.0
            if is_metric > 0:
                degradation = ((is_metric - oos_metric) / is_metric) * 100
                
            logger.info(f"  └─ Test {optimize_metric}: {oos_metric:.2f} (Degradation: {degradation:.1f}%)")
            
            oos_trades.extend(oos_result.trades)
            param_history.append({
                "split_idx": i,
                "test_start": split.test_start,
                "test_end": split.test_end,
                "best_params": best_params,
                "is_metric": is_metric,
                "oos_metric": oos_metric,
                "degradation_pct": degradation,
            })
            in_sample_results.append(is_result)
            out_of_sample_results.append(oos_result)

        # Step 3: Stitch together the total Out-of-Sample performance
        if not splits:
            return {}
            
        total_oos_result = self.engine.summarize(
            symbol=symbol,
            period_start=splits[0].test_start,
            period_end=splits[-1].test_end,
            trades=oos_trades,
        )

        return {
            "total_oos_result": total_oos_result,
            "param_history": param_history,
            "splits_count": len(splits),
        }
