"""
Alpaca Broker Abstraction Layer.

Handles all communication with Alpaca's Trading and Market Data APIs.
Supports both paper and live trading modes.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopOrderRequest,
    StopLimitOrderRequest,
    TrailingStopOrderRequest,
    GetOrdersRequest,
    ClosePositionRequest,
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderType as AlpacaOrderType,
    OrderStatus as AlpacaOrderStatus,
    QueryOrderStatus,
)
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestQuoteRequest,
    StockSnapshotRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.live import StockDataStream

from src.core.models import (
    Order, Position, Side, OrderType, OrderStatus, TradingMode,
)


# ─── Timeframe Mapping ───────────────────────────────────────────────

TIMEFRAME_MAP = {
    "1Min": TimeFrame(1, TimeFrameUnit.Minute),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    "30Min": TimeFrame(30, TimeFrameUnit.Minute),
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
    "1Day": TimeFrame(1, TimeFrameUnit.Day),
}


class AlpacaBroker:
    """
    Alpaca broker wrapper for trading and data operations.

    Provides a clean interface for:
    - Placing/canceling/modifying orders
    - Fetching account info, positions, and orders
    - Getting historical and real-time market data
    """

    def __init__(self, mode: TradingMode = TradingMode.PAPER):
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")

        if not api_key or not secret_key:
            raise ValueError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment. "
                "Get your keys from https://app.alpaca.markets"
            )

        self.mode = mode
        is_paper = mode == TradingMode.PAPER

        # Trading client
        self.trading_client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=is_paper,
        )

        # Data client (no auth needed for free tier, but auth gives more data)
        self.data_client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=secret_key,
        )

        # WebSocket stream for real-time data
        self._stream: Optional[StockDataStream] = None

        logger.info(f"Alpaca broker initialized in {'PAPER' if is_paper else '🔴 LIVE'} mode")

    # ─── Account ──────────────────────────────────────────────────

    def get_account(self) -> dict:
        """Get account information including buying power, equity, etc."""
        account = self.trading_client.get_account()
        return {
            "id": account.id,
            "status": account.status,
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "portfolio_value": float(account.portfolio_value),
            "day_trade_count": account.daytrade_count,
            "pattern_day_trader": account.pattern_day_trader,
            "trading_blocked": account.trading_blocked,
            "account_blocked": account.account_blocked,
        }

    def get_buying_power(self) -> float:
        """Get current buying power."""
        account = self.trading_client.get_account()
        return float(account.buying_power)

    def get_equity(self) -> float:
        """Get current account equity."""
        account = self.trading_client.get_account()
        return float(account.equity)

    # ─── Orders ───────────────────────────────────────────────────

    def place_order(self, order: Order) -> Order:
        """
        Place an order with Alpaca.
        Returns the order with updated ID and status.
        """
        side = OrderSide.BUY if order.side == Side.BUY else OrderSide.SELL

        try:
            if order.order_type == OrderType.MARKET:
                request = MarketOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )
            elif order.order_type == OrderType.LIMIT:
                request = LimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=order.limit_price,
                )
            elif order.order_type == OrderType.STOP:
                request = StopOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    stop_price=order.stop_price,
                )
            elif order.order_type == OrderType.STOP_LIMIT:
                request = StopLimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    stop_price=order.stop_price,
                    limit_price=order.limit_price,
                )
            elif order.order_type == OrderType.TRAILING_STOP:
                request = TrailingStopOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    trail_percent=order.stop_price,  # Using stop_price field for trail %
                )
            else:
                raise ValueError(f"Unsupported order type: {order.order_type}")

            result = self.trading_client.submit_order(request)

            order.id = str(result.id)
            order.status = self._map_order_status(result.status)
            if result.filled_avg_price:
                order.filled_price = float(result.filled_avg_price)
            if result.filled_qty:
                order.filled_quantity = int(float(result.filled_qty))

            logger.info(
                f"Order placed: {order.side.value} {order.quantity}x {order.symbol} "
                f"@ {order.order_type.value} | ID: {order.id} | Status: {order.status.value}"
            )
            return order

        except Exception as e:
            order.status = OrderStatus.REJECTED
            logger.error(f"Order REJECTED for {order.symbol}: {e}")
            raise

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID."""
        try:
            self.trading_client.cancel_order_by_id(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> bool:
        """Cancel all open orders."""
        try:
            self.trading_client.cancel_orders()
            logger.info("All open orders cancelled")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return False

    def get_order(self, order_id: str) -> dict:
        """Get order status by ID."""
        order = self.trading_client.get_order_by_id(order_id)
        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": float(order.qty),
            "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
            "filled_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "status": order.status.value,
            "created_at": order.created_at,
        }

    def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = self.trading_client.get_orders(request)
        return [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty),
                "status": o.status.value,
                "type": o.type.value,
            }
            for o in orders
        ]

    # ─── Positions ───────────────────────────────────────────────

    def get_positions(self) -> list[Position]:
        """Get all open positions as Position models."""
        raw_positions = self.trading_client.get_all_positions()
        positions = []
        for p in raw_positions:
            pos = Position(
                symbol=p.symbol,
                side=Side.BUY if float(p.qty) > 0 else Side.SELL,
                quantity=abs(int(float(p.qty))),
                entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                unrealized_pnl=float(p.unrealized_pl),
                unrealized_pnl_pct=float(p.unrealized_plpc) * 100,
            )
            positions.append(pos)
        return positions

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a specific symbol."""
        try:
            p = self.trading_client.get_open_position(symbol)
            return Position(
                symbol=p.symbol,
                side=Side.BUY if float(p.qty) > 0 else Side.SELL,
                quantity=abs(int(float(p.qty))),
                entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                unrealized_pnl=float(p.unrealized_pl),
                unrealized_pnl_pct=float(p.unrealized_plpc) * 100,
            )
        except Exception:
            return None

    def close_position(self, symbol: str) -> bool:
        """Close an open position entirely."""
        try:
            self.trading_client.close_position(
                symbol,
                close_options=ClosePositionRequest(qty=None, percentage=None),
            )
            logger.info(f"Position closed: {symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to close position {symbol}: {e}")
            return False

    def close_all_positions(self) -> bool:
        """Close all open positions (end-of-day square-off)."""
        try:
            self.trading_client.close_all_positions(cancel_orders=True)
            logger.info("All positions closed (end-of-day square-off)")
            return True
        except Exception as e:
            logger.error(f"Failed to close all positions: {e}")
            return False

    # ─── Market Data ──────────────────────────────────────────────

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "5Min",
        lookback_days: int = 5,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Get historical OHLCV bars as a DataFrame.

        Args:
            symbol: Stock ticker (e.g., "AAPL")
            timeframe: Candle interval ("1Min", "5Min", "15Min", "1Hour", "1Day")
            lookback_days: Number of calendar days to look back
            limit: Max number of bars to return

        Returns:
            DataFrame with columns: open, high, low, close, volume, vwap, trade_count
        """
        tf = TIMEFRAME_MAP.get(timeframe)
        if not tf:
            raise ValueError(f"Unsupported timeframe: {timeframe}. Options: {list(TIMEFRAME_MAP.keys())}")

        start = datetime.utcnow() - timedelta(days=lookback_days)

        request_params = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            limit=limit,
        )

        bars = self.data_client.get_stock_bars(request_params)
        df = bars.df

        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel("symbol")

        df.index = pd.to_datetime(df.index)
        df.index = df.index.tz_localize(None) if df.index.tz else df.index

        return df

    def get_bars_multi(
        self,
        symbols: list[str],
        timeframe: str = "5Min",
        lookback_days: int = 5,
    ) -> dict[str, pd.DataFrame]:
        """
        Get historical bars for multiple symbols.

        Returns:
            Dict mapping symbol -> DataFrame
        """
        tf = TIMEFRAME_MAP.get(timeframe)
        if not tf:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        start = datetime.utcnow() - timedelta(days=lookback_days)

        request_params = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=tf,
            start=start,
        )

        bars = self.data_client.get_stock_bars(request_params)
        df = bars.df

        result = {}
        if isinstance(df.index, pd.MultiIndex):
            for symbol in symbols:
                try:
                    symbol_df = df.loc[symbol].copy()
                    symbol_df.index = pd.to_datetime(symbol_df.index)
                    symbol_df.index = symbol_df.index.tz_localize(None) if symbol_df.index.tz else symbol_df.index
                    result[symbol] = symbol_df
                except KeyError:
                    logger.warning(f"No data for {symbol}")
        else:
            # Single symbol case
            if symbols:
                df.index = pd.to_datetime(df.index)
                df.index = df.index.tz_localize(None) if df.index.tz else df.index
                result[symbols[0]] = df

        return result

    def get_latest_quote(self, symbol: str) -> dict:
        """Get the latest quote (bid/ask) for a symbol."""
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self.data_client.get_stock_latest_quote(request)
        quote = quotes[symbol]
        return {
            "symbol": symbol,
            "bid": float(quote.bid_price),
            "ask": float(quote.ask_price),
            "bid_size": quote.bid_size,
            "ask_size": quote.ask_size,
            "mid": (float(quote.bid_price) + float(quote.ask_price)) / 2,
            "spread": float(quote.ask_price) - float(quote.bid_price),
        }

    def get_snapshot(self, symbol: str) -> dict:
        """Get a complete snapshot for a symbol (quote + latest bar + prev close)."""
        request = StockSnapshotRequest(symbol_or_symbols=symbol)
        snapshots = self.data_client.get_stock_snapshot(request)
        snap = snapshots[symbol]
        return {
            "symbol": symbol,
            "latest_trade_price": float(snap.latest_trade.price) if snap.latest_trade else None,
            "latest_trade_size": snap.latest_trade.size if snap.latest_trade else None,
            "bid": float(snap.latest_quote.bid_price) if snap.latest_quote else None,
            "ask": float(snap.latest_quote.ask_price) if snap.latest_quote else None,
            "minute_bar": {
                "open": float(snap.minute_bar.open),
                "high": float(snap.minute_bar.high),
                "low": float(snap.minute_bar.low),
                "close": float(snap.minute_bar.close),
                "volume": float(snap.minute_bar.volume),
            } if snap.minute_bar else None,
            "daily_bar": {
                "open": float(snap.daily_bar.open),
                "high": float(snap.daily_bar.high),
                "low": float(snap.daily_bar.low),
                "close": float(snap.daily_bar.close),
                "volume": float(snap.daily_bar.volume),
            } if snap.daily_bar else None,
            "prev_daily_bar": {
                "close": float(snap.previous_daily_bar.close),
                "volume": float(snap.previous_daily_bar.volume),
            } if snap.previous_daily_bar else None,
        }

    # ─── Clock & Calendar ─────────────────────────────────────────

    def is_market_open(self) -> bool:
        """Check if the US stock market is currently open."""
        clock = self.trading_client.get_clock()
        return clock.is_open

    def get_clock(self) -> dict:
        """Get market clock info."""
        clock = self.trading_client.get_clock()
        return {
            "is_open": clock.is_open,
            "next_open": clock.next_open,
            "next_close": clock.next_close,
            "timestamp": clock.timestamp,
        }

    def get_next_market_open(self) -> datetime:
        """Get the next market open time."""
        clock = self.trading_client.get_clock()
        return clock.next_open

    def get_next_market_close(self) -> datetime:
        """Get the next market close time."""
        clock = self.trading_client.get_clock()
        return clock.next_close

    # ─── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _map_order_status(alpaca_status) -> OrderStatus:
        """Map Alpaca order status to our internal OrderStatus."""
        mapping = {
            AlpacaOrderStatus.NEW: OrderStatus.PENDING,
            AlpacaOrderStatus.ACCEPTED: OrderStatus.PENDING,
            AlpacaOrderStatus.PENDING_NEW: OrderStatus.PENDING,
            AlpacaOrderStatus.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
            AlpacaOrderStatus.FILLED: OrderStatus.FILLED,
            AlpacaOrderStatus.CANCELED: OrderStatus.CANCELLED,
            AlpacaOrderStatus.REJECTED: OrderStatus.REJECTED,
            AlpacaOrderStatus.EXPIRED: OrderStatus.EXPIRED,
        }
        return mapping.get(alpaca_status, OrderStatus.PENDING)
