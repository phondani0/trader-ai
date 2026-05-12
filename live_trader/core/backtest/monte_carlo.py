"""
Monte Carlo Simulation Engine.

Analyzes strategy robustness by resampling historical trade returns
to generate a distribution of possible future outcomes, identifying
confidence intervals for worst-case drawdowns.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np


class MonteCarloAnalyzer:
    """Monte Carlo simulation for strategy robustness."""

    def __init__(self, n_simulations: int = 1000, confidence: float = 0.95):
        self.n_simulations = n_simulations
        self.confidence = confidence

    def bootstrap_returns(
        self,
        returns: List[float],
        n_periods: int | None = None,
    ) -> np.ndarray:
        """
        Bootstrap simulation by resampling returns.

        Args:
            returns: Historical trade returns (percentage floats e.g., 5.0 for 5%).
            n_periods: Length of each simulation (default: same as input length).

        Returns:
            Array of shape (n_simulations, n_periods)
        """
        if not returns:
            return np.array([])
            
        if n_periods is None:
            n_periods = len(returns)

        # Convert percentage returns to decimal
        returns_arr = np.array(returns) / 100.0
        
        simulations = np.zeros((self.n_simulations, n_periods))

        for i in range(self.n_simulations):
            # Resample with replacement
            simulated_returns = np.random.choice(
                returns_arr,
                size=n_periods,
                replace=True
            )
            simulations[i] = simulated_returns

        return simulations

    def analyze_drawdowns(
        self,
        returns: List[float],
    ) -> Dict[str, float]:
        """
        Analyze drawdown distribution via simulation.
        
        Returns metrics in percentage format (e.g., -15.5 for -15.5% MDD).
        """
        if len(returns) < 5:
            return {
                "expected_max_dd_pct": 0.0,
                "median_max_dd_pct": 0.0,
                f"worst_{int(self.confidence*100)}pct_mdd": 0.0,
                "worst_case_mdd_pct": 0.0,
            }
            
        simulations = self.bootstrap_returns(returns)
        
        max_drawdowns = []
        for sim_returns in simulations:
            equity = (1 + sim_returns).cumprod()
            # Handle edge case where equity drops to 0 or below
            equity = np.maximum(equity, 0.0001)
            rolling_max = np.maximum.accumulate(equity)
            drawdowns = (equity - rolling_max) / rolling_max
            max_drawdowns.append(drawdowns.min() * 100)  # Convert back to percentage

        max_drawdowns = np.array(max_drawdowns)

        return {
            "expected_max_dd_pct": float(np.mean(max_drawdowns)),
            "median_max_dd_pct": float(np.median(max_drawdowns)),
            f"worst_{int(self.confidence*100)}pct_mdd": float(
                np.percentile(max_drawdowns, (1 - self.confidence) * 100)
            ),
            "worst_case_mdd_pct": float(max_drawdowns.min()),
        }
