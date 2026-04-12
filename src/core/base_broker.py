"""
Base Broker — Abstract Interface.

Defines the universal contract that ALL broker implementations must satisfy.
The rest of the codebase depends ONLY on this interface, never on a concrete broker.

Capabilities (not conditionals):
    Instead of `if asset_class == "crypto"`, consumers ask:
        broker.supports_short_selling
        broker.is_24_7_market
    The broker answers. The consumer never checks implementation details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from src.core.models import (
    Order,
    OrderType,
    Position,
    Side,
)


class BaseBroker(ABC):
    """
    Abstract broker interface.

    Every broker (Alpaca, CCXT/Bybit, IBKR, etc.) must implement this contract.
    The trading engine, data feed, scheduler, risk manager, and kill switch
    all depend on this interface — never on a concrete class.
    """

    # ─── Capability Properties ────────────────────────────────────
    # These replace ALL `if asset_class == "crypto"` checks in the codebase.

    @property
    @abstractmethod
    def supports_short_selling(self) -> bool:
        """Whether this broker/asset-class combo allows short selling."""
        ...

    @property
    @abstractmethod
    def is_24_7_market(self) -> bool:
        """Whether the market is open 24/7 (crypto) vs session-based (equities)."""
        ...

    @property
    @abstractmethod
    def supported_order_types(self) -> list[OrderType]:
        """Which order types this broker supports (MARKET, LIMIT, STOP, STOP_LIMIT, etc.)."""
        ...

    # ─── Account ──────────────────────────────────────────────────

    @abstractmethod
    def get_buying_power(self) -> float:
        """Get current buying power (factoring leverage if applicable)."""
        ...

    @abstractmethod
    def get_equity(self) -> float:
        """Get total account equity."""
        ...

    # ─── Orders ───────────────────────────────────────────────────

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """
        Place an order. Returns the order with updated ID and status.
        Each broker handles its own order type translation internally.
        """
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID."""
        ...

    @abstractmethod
    def cancel_all_orders(self) -> bool:
        """Cancel all open orders."""
        ...

    # ─── Positions ────────────────────────────────────────────────

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Get all open positions."""
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a specific symbol."""
        ...

    @abstractmethod
    def close_position(self, symbol: str) -> bool:
        """Close an open position entirely."""
        ...

    @abstractmethod
    def close_all_positions(self) -> bool:
        """Close all open positions."""
        ...

    # ─── Market Data ──────────────────────────────────────────────

    @abstractmethod
    def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 1000, since: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data.

        Args:
            symbol: Trading pair (e.g. "BTC/USD", "AAPL")
            timeframe: Candle interval in CCXT format ("1m", "5m", "1h", "1d")
            limit: Max number of candles to return

        Returns:
            DataFrame with columns: open, high, low, close, volume
            Index: datetime
        """
        ...

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> dict:
        """
        Get the latest ticker data for a symbol.

        Returns:
            dict with at least: {"bid": float, "ask": float, "last": float}
        """
        ...

    @abstractmethod
    def fetch_tickers(self, symbols: Optional[list[str]] = None) -> dict[str, dict]:
        """
        Get latest tickers for multiple symbols in bulk.

        Args:
            symbols: Optional list of symbols. If None, fetches all available.

        Returns:
            dict mapping symbol -> ticker dict
        """
        ...

    # ─── Clock ────────────────────────────────────────────────────

    @abstractmethod
    def is_market_open(self) -> bool:
        """Check if the market is currently open for trading."""
        ...

    # ─── Stop-Loss Factory ────────────────────────────────────────
    # This is the key: each broker knows HOW to build a compliant stop-loss.
    # The engine just calls this method — no if/else about asset class.

    def create_stop_loss_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        stop_price: float,
        strategy_name: str = "",
    ) -> Order:
        """
        Build a broker-compliant stop-loss order.

        Default implementation uses a STOP order.
        Brokers that don't support STOP (e.g. Alpaca crypto) override this
        to use STOP_LIMIT with appropriate slippage.
        """
        return Order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.STOP,
            stop_price=stop_price,
            strategy_name=strategy_name,
        )
