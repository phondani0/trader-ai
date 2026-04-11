"""
Abstract Base Strategy.

All trading strategies inherit from this class. Provides a standard
interface for signal generation, enabling the strategy manager to
iterate over strategies uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import pandas as pd
from loguru import logger

from src.core.models import Signal, SignalType, Side, MarketRegime


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Every strategy must implement:
        - name: Unique strategy identifier
        - supported_regimes: List of MarketRegime where this strategy is active
        - generate_signal(): Analyze data and return a Signal or None
    """

    def __init__(self, params: dict):
        """
        Args:
            params: Strategy-specific parameters from strategies.yaml
        """
        self.params = params
        self.enabled = params.get("enabled", True)
        self._supported_regimes = [
            MarketRegime(r) if r != "all" else None
            for r in params.get("regime", ["all"])
        ]

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name (e.g., 'macd_bollinger')."""
        ...

    @property
    def supported_regimes(self) -> list[MarketRegime]:
        """Market regimes where this strategy should be active."""
        if None in self._supported_regimes:
            return list(MarketRegime)  # Active in all regimes
        return self._supported_regimes

    def should_run(self, regime: MarketRegime) -> bool:
        """Check if this strategy should run in the current market regime."""
        if not self.enabled:
            return False
        if None in self._supported_regimes:
            return True  # "all" regime
        return regime in self.supported_regimes

    @abstractmethod
    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        """
        Analyze the data and generate a trading signal.

        Args:
            symbol: Stock ticker
            df: OHLCV DataFrame with all indicators pre-computed
            regime: Current market regime

        Returns:
            Signal if a trade setup is found, None otherwise
        """
        ...

    def _create_signal(
        self,
        symbol: str,
        signal_type: SignalType,
        side: Side,
        entry_price: float,
        stop_loss: float,
        take_profit: Optional[float] = None,
        confidence: float = 0.5,
        regime: MarketRegime = MarketRegime.UNKNOWN,
        reason: str = "",
        metadata: Optional[dict] = None,
    ) -> Signal:
        """Helper to create a Signal with common fields pre-filled."""
        return Signal(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            strategy_name=self.name,
            signal_type=signal_type,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            regime=regime,
            reason=reason,
            metadata=metadata or {},
        )

    def _check_risk_reward(self, entry: float, stop_loss: float, take_profit: float, min_rr: float = 1.5) -> bool:
        """Validate that the risk:reward ratio meets minimum threshold."""
        risk = abs(entry - stop_loss)
        if risk == 0:
            return False
        reward = abs(take_profit - entry)
        return (reward / risk) >= min_rr

    def __repr__(self):
        return f"<{self.__class__.__name__}(name={self.name}, enabled={self.enabled})>"
