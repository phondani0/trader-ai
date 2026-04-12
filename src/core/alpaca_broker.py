"""
Alpaca Broker Implementation.

Connects to Alpaca's Trading and Market Data APIs for US equities and crypto spot.
All Alpaca-specific quirks (TimeInForce, STOP vs STOP_LIMIT, symbol formats)
are handled HERE — never leaked to consumers.
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
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderType as AlpacaOrderType,
    OrderStatus as AlpacaOrderStatus,
    QueryOrderStatus,
)
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    CryptoBarsRequest,
    StockLatestQuoteRequest,
    CryptoLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from src.core.base_broker import BaseBroker
from src.core.models import (
    Order, Position, Side, OrderType, OrderStatus, TradingMode,
)


# ─── Timeframe Mapping ───────────────────────────────────────────────

TIMEFRAME_MAP = {
    "1m": TimeFrame(1, TimeFrameUnit.Minute),
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "30m": TimeFrame(30, TimeFrameUnit.Minute),
    "1h": TimeFrame(1, TimeFrameUnit.Hour),
    "1d": TimeFrame(1, TimeFrameUnit.Day),
}


class AlpacaBroker(BaseBroker):
    """
    Alpaca broker for US equities and crypto spot.
    """

    def __init__(self, mode: TradingMode = TradingMode.PAPER, config: Optional[dict] = None):
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")

        if not api_key or not secret_key:
            raise ValueError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment. "
                "Get your keys from https://app.alpaca.markets"
            )

        self.mode = mode
        is_paper = mode == TradingMode.PAPER

        # Determine asset class from config
        if config:
            self._asset_class = config.get("trading", {}).get("asset_class", "equity").lower()
        else:
            self._asset_class = "equity"

        self._is_crypto = self._asset_class == "crypto"

        # Trading client
        self.trading_client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=is_paper,
        )

        # Data client — selected based on asset class
        if self._is_crypto:
            self.data_client = CryptoHistoricalDataClient(
                api_key=api_key, secret_key=secret_key,
            )
        else:
            self.data_client = StockHistoricalDataClient(
                api_key=api_key, secret_key=secret_key,
            )

        logger.info(
            f"Alpaca broker initialized in {'PAPER' if is_paper else '🔴 LIVE'} mode "
            f"(Asset: {self._asset_class})"
        )

    # ─── Capability Properties ────────────────────────────────────

    @property
    def supports_short_selling(self) -> bool:
        # Alpaca: equities support shorting, crypto does NOT
        return not self._is_crypto

    @property
    def is_24_7_market(self) -> bool:
        return self._is_crypto

    @property
    def supported_order_types(self) -> list[OrderType]:
        if self._is_crypto:
            return [OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LIMIT]
        return [OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP]

    # ─── Account ──────────────────────────────────────────────────

    def get_buying_power(self) -> float:
        account = self.trading_client.get_account()
        return float(account.buying_power)

    def get_equity(self) -> float:
        account = self.trading_client.get_account()
        return float(account.equity)

    # ─── Orders ───────────────────────────────────────────────────

    def place_order(self, order: Order) -> Order:
        side = OrderSide.BUY if order.side == Side.BUY else OrderSide.SELL
        tif = TimeInForce.GTC if self._is_crypto else TimeInForce.DAY

        try:
            if order.order_type == OrderType.MARKET:
                request = MarketOrderRequest(
                    symbol=order.symbol, qty=order.quantity,
                    side=side, time_in_force=tif,
                )
            elif order.order_type == OrderType.LIMIT:
                request = LimitOrderRequest(
                    symbol=order.symbol, qty=order.quantity,
                    side=side, time_in_force=tif,
                    limit_price=order.limit_price,
                )
            elif order.order_type == OrderType.STOP:
                request = StopOrderRequest(
                    symbol=order.symbol, qty=order.quantity,
                    side=side, time_in_force=tif,
                    stop_price=order.stop_price,
                )
            elif order.order_type == OrderType.STOP_LIMIT:
                request = StopLimitOrderRequest(
                    symbol=order.symbol, qty=order.quantity,
                    side=side, time_in_force=tif,
                    stop_price=order.stop_price,
                    limit_price=order.limit_price,
                )
            elif order.order_type == OrderType.TRAILING_STOP:
                request = TrailingStopOrderRequest(
                    symbol=order.symbol, qty=order.quantity,
                    side=side, time_in_force=tif,
                    trail_percent=order.stop_price,
                )
            else:
                raise ValueError(f"Unsupported order type: {order.order_type}")

            result = self.trading_client.submit_order(request)

            order.id = str(result.id)
            order.status = self._map_order_status(result.status)
            if result.filled_avg_price:
                order.filled_price = float(result.filled_avg_price)
            if result.filled_qty:
                order.filled_quantity = float(result.filled_qty)

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
        try:
            self.trading_client.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> bool:
        try:
            self.trading_client.cancel_orders()
            logger.info("All open orders cancelled")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return False

    # ─── Positions ────────────────────────────────────────────────

    def get_positions(self) -> list[Position]:
        raw_positions = self.trading_client.get_all_positions()
        positions = []
        for p in raw_positions:
            pos = Position(
                symbol=p.symbol,
                side=Side.BUY if float(p.qty) > 0 else Side.SELL,
                quantity=abs(float(p.qty)),
                entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                unrealized_pnl=float(p.unrealized_pl),
                unrealized_pnl_pct=float(p.unrealized_plpc) * 100,
            )
            positions.append(pos)
        return positions

    def get_position(self, symbol: str) -> Optional[Position]:
        try:
            p = self.trading_client.get_open_position(symbol)
            return Position(
                symbol=p.symbol,
                side=Side.BUY if float(p.qty) > 0 else Side.SELL,
                quantity=abs(float(p.qty)),
                entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                unrealized_pnl=float(p.unrealized_pl),
                unrealized_pnl_pct=float(p.unrealized_plpc) * 100,
            )
        except Exception:
            return None

    def close_position(self, symbol: str) -> bool:
        try:
            self.trading_client.close_position(symbol_or_asset_id=symbol)
            logger.info(f"Position closed: {symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to close position {symbol}: {e}")
            return False

    def close_all_positions(self) -> bool:
        try:
            self.trading_client.close_all_positions(cancel_orders=True)
            logger.info("All positions closed")
            return True
        except Exception as e:
            logger.error(f"Failed to close all positions: {e}")
            return False

    # ─── Market Data ──────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 1000) -> pd.DataFrame:
        tf = TIMEFRAME_MAP.get(timeframe)
        if not tf:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        # Estimate lookback from limit
        lookback_days = max(5, limit // 288 + 1)  # 288 = 5m bars per day
        start = datetime.utcnow() - timedelta(days=lookback_days)

        if self._is_crypto:
            request = CryptoBarsRequest(
                symbol_or_symbols=symbol, timeframe=tf,
                start=start, limit=limit,
            )
            bars = self.data_client.get_crypto_bars(request)
        else:
            request = StockBarsRequest(
                symbol_or_symbols=symbol, timeframe=tf,
                start=start, limit=limit,
            )
            bars = self.data_client.get_stock_bars(request)

        df = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel("symbol")
        df.index = pd.to_datetime(df.index)
        df.index = df.index.tz_localize(None) if df.index.tz else df.index
        return df

    def fetch_ticker(self, symbol: str) -> dict:
        if self._is_crypto:
            request = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self.data_client.get_crypto_latest_quote(request)
        else:
            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self.data_client.get_stock_latest_quote(request)

        quote = quotes[symbol]
        bid = float(quote.bid_price)
        ask = float(quote.ask_price)
        return {
            "bid": bid,
            "ask": ask,
            "last": (bid + ask) / 2,
        }

    # ─── Clock ────────────────────────────────────────────────────

    def is_market_open(self) -> bool:
        if self._is_crypto:
            return True
        clock = self.trading_client.get_clock()
        return clock.is_open

    # ─── Stop-Loss Factory ────────────────────────────────────────
    # Alpaca crypto doesn't support standard STOP orders — must use STOP_LIMIT.
    # This is handled HERE so the engine never needs to know.

    def create_stop_loss_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        stop_price: float,
        strategy_name: str = "",
    ) -> Order:
        if self._is_crypto:
            # 1% slippage buffer for the limit price
            if side == Side.SELL:
                limit_price = stop_price * 0.99
            else:
                limit_price = stop_price * 1.01

            return Order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=OrderType.STOP_LIMIT,
                stop_price=stop_price,
                limit_price=limit_price,
                strategy_name=strategy_name,
            )
        else:
            # Standard STOP order for equities
            return Order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=OrderType.STOP,
                stop_price=stop_price,
                strategy_name=strategy_name,
            )

    # ─── Internal ─────────────────────────────────────────────────

    @staticmethod
    def _map_order_status(alpaca_status) -> OrderStatus:
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
