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

from src.core.indicator_engine import (
    adx_cols,
    atr_col,
    atr_pct_col,
    bbands_cols,
    ema_col,
    macd_cols,
    rsi_col,
    sma_col,
    supertrend_cols,
    volume_ratio_col,
    volume_sma_col,
)
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
        self._benchmark_approved_regimes: set[MarketRegime] | None = None

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
            supported = True
        else:
            supported = regime in self.supported_regimes

        if not supported:
            return False

        if self._benchmark_approved_regimes is not None:
            return regime in self._benchmark_approved_regimes

        return True

    def indicator_requirements(self) -> dict[str, object]:
        """Indicators required by this strategy."""
        return {}

    def set_benchmark_approved_regimes(self, regimes: list[str] | None):
        """Restrict runtime execution to benchmark-approved regimes."""
        if regimes is None:
            self._benchmark_approved_regimes = None
            return

        approved_regimes: set[MarketRegime] = set()
        for regime_name in regimes:
            try:
                approved_regimes.add(MarketRegime(regime_name))
            except ValueError:
                continue
        self._benchmark_approved_regimes = approved_regimes

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

    @staticmethod
    def ema_col(period: int) -> str:
        return ema_col(period)

    @staticmethod
    def sma_col(period: int) -> str:
        return sma_col(period)

    @staticmethod
    def rsi_col(period: int) -> str:
        return rsi_col(period)

    @staticmethod
    def atr_col(period: int) -> str:
        return atr_col(period)

    @staticmethod
    def atr_pct_col(period: int) -> str:
        return atr_pct_col(period)

    @staticmethod
    def adx_cols(period: int) -> dict[str, str]:
        return adx_cols(period)

    @staticmethod
    def macd_cols(fast: int, slow: int, signal: int) -> dict[str, str]:
        return macd_cols(fast, slow, signal)

    @staticmethod
    def bbands_cols(length: int, std_dev: float) -> dict[str, str]:
        return bbands_cols(length, std_dev)

    @staticmethod
    def supertrend_cols(length: int, multiplier: float) -> dict[str, str]:
        return supertrend_cols(length, multiplier)

    @staticmethod
    def volume_sma_col(period: int) -> str:
        return volume_sma_col(period)

    @staticmethod
    def volume_ratio_col(period: int) -> str:
        return volume_ratio_col(period)
