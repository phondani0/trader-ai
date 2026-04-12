"""
CCXT Broker Implementation.

Connects to global crypto exchanges (Bybit, Binance, Kraken, etc.) via the
CCXT unified standard. Enables Perpetual Futures, short selling, and leverage.

All exchange-specific quirks are handled HERE — never leaked to consumers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd
import yaml
from loguru import logger

from src.core.base_broker import BaseBroker
from src.core.models import (
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TradingMode,
)

# ─── Timeframe Mapping (internal → CCXT) ─────────────────────────────

TIMEFRAME_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "1d": "1d",
}


class CCXTBroker(BaseBroker):
    """
    CCXT broker for crypto perpetual futures.
    Supports short selling, leverage, and 24/7 operation.
    """

    def __init__(
        self, mode: TradingMode = TradingMode.PAPER, config: Optional[dict] = None
    ):
        api_key = os.getenv("EXCHANGE_API_KEY")
        secret_key = os.getenv("EXCHANGE_SECRET_KEY")
        exchange_id = os.getenv("EXCHANGE_ID", "bybit")

        self.mode = mode
        is_paper = mode == TradingMode.PAPER

        exchange_class = getattr(ccxt, exchange_id)

        # Simplified config: pass keys if they exist, regardless of content
        exchange_opts: dict = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        }
        if api_key and secret_key:
            exchange_opts["apiKey"] = api_key
            exchange_opts["secret"] = secret_key

        self.exchange = exchange_class(exchange_opts)

        if is_paper:
            if exchange_id == "bybit":
                self.exchange.enable_demo_trading(True)
            elif hasattr(self.exchange, "set_sandbox_mode"):
                self.exchange.set_sandbox_mode(True)

        self.exchange.load_markets()

        if config:
            risk = config.get("risk_management", {})
            self._leverage = risk.get("leverage", 1)
        else:
            config_path = (
                Path(__file__).parent.parent.parent / "config" / "settings.yaml"
            )
            try:
                with open(config_path, "r") as f:
                    settings = yaml.safe_load(f)
                self._leverage = settings.get("risk_management", {}).get("leverage", 1)
            except Exception:
                self._leverage = 1

        logger.info(
            f"CCXT Broker initialized on {exchange_id.upper()} in "
            f"{'PAPER' if is_paper else '🔴 LIVE'} mode (Leverage: {self._leverage}x)"
        )

    # ─── Capability Properties ────────────────────────────────────

    @property
    def supports_short_selling(self) -> bool:
        return True  # Perpetual futures natively support shorts

    @property
    def is_24_7_market(self) -> bool:
        return True  # Crypto never sleeps

    @property
    def supported_order_types(self) -> list[OrderType]:
        return [OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LIMIT]

    # ─── Account ──────────────────────────────────────────────────

    def get_buying_power(self) -> float:
        balance = self.exchange.fetch_balance()
        usdt_free = float(balance.get("USDT", {}).get("free", 0.0) or 0.0)
        return usdt_free * self._leverage

    def get_equity(self) -> float:
        balance = self.exchange.fetch_balance()
        return float(balance.get("USDT", {}).get("total", 0.0) or 0.0)

    # ─── Orders ───────────────────────────────────────────────────

    def place_order(self, order: Order) -> Order:
        symbol = order.symbol
        side = "buy" if order.side == Side.BUY else "sell"

        if order.order_type in (OrderType.STOP_LIMIT, OrderType.LIMIT):
            order_type = "limit"
        else:
            order_type = "market"

        params = {}
        if order.stop_price:
            params["stopLoss"] = order.stop_price

        try:
            try:
                self.exchange.set_leverage(self._leverage, symbol)
            except Exception:
                pass  # Already set or unsupported

            res = self.exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=order.quantity,
                price=order.limit_price,
                params=params,
            )

            logger.info(f"Order response: {res}")
            order.id = str(res["id"])
            order.status = self._map_status(res.get("status", "open"))
            order.filled_quantity = float(res.get("filled", 0.0) or 0.0)
            order.filled_price = float(res.get("average", 0.0) or 0.0)
            logger.info(
                f"Order placed: {side.upper()} {order.quantity}x {symbol} "
                f"@ {order_type} | ID: {order.id}"
            )
            return order
        except Exception as e:
            order.status = OrderStatus.REJECTED
            logger.error(f"Order REJECTED for {symbol}: {e}")
            raise

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.exchange.cancel_order(order_id)
            return True
        except Exception:
            return False

    def cancel_all_orders(self) -> bool:
        try:
            self.exchange.cancel_all_orders()
            return True
        except Exception:
            return False

    # ─── Positions ────────────────────────────────────────────────

    def get_positions(self) -> list[Position]:
        try:
            pos_data = self.exchange.fetch_positions()
            positions = []
            for p in pos_data:
                qty = float(
                    p.get("contracts", 0.0) or p.get("info", {}).get("size", 0.0) or 0.0
                )
                if qty == 0.0:
                    continue

                side_str = str(p.get("side", "")).lower()
                side = Side.BUY if side_str == "long" else Side.SELL

                pos = Position(
                    symbol=p["symbol"],
                    side=side,
                    quantity=qty,
                    entry_price=float(p.get("entryPrice", 0.0) or 0.0),
                    current_price=float(p.get("markPrice", 0.0) or 0.0),
                    unrealized_pnl=float(p.get("unrealizedPnl", 0.0) or 0.0),
                    unrealized_pnl_pct=float(p.get("percentage", 0.0) or 0.0),
                )
                positions.append(pos)
            return positions
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []

    def get_position(self, symbol: str) -> Optional[Position]:
        for p in self.get_positions():
            if p.symbol == symbol:
                return p
        return None

    def close_position(self, symbol: str) -> bool:
        pos = self.get_position(symbol)
        if not pos:
            return False

        closing_side = Side.SELL if pos.side == Side.BUY else Side.BUY
        closing_order = Order(
            symbol=symbol,
            side=closing_side,
            quantity=pos.quantity,
            order_type=OrderType.MARKET,
            strategy_name="liquidation_override",
        )
        try:
            self.place_order(closing_order)
            logger.info(f"Position closed: {symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to close position {symbol}: {e}")
            return False

    def close_all_positions(self) -> bool:
        positions = self.get_positions()
        success = True
        for p in positions:
            if not self.close_position(p.symbol):
                success = False
        return success

    # ─── Market Data ──────────────────────────────────────────────

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 1000
    ) -> pd.DataFrame:
        tf = TIMEFRAME_MAP.get(timeframe, timeframe)
        ohlcv = self.exchange.fetch_ohlcv(symbol, tf, limit=limit)

        df = pd.DataFrame(
            ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    def fetch_ticker(self, symbol: str) -> dict:
        ticker = self.exchange.fetch_ticker(symbol)
        return {
            "bid": float(ticker.get("bid", 0.0) or 0.0),
            "ask": float(ticker.get("ask", 0.0) or 0.0),
            "last": float(ticker.get("last", 0.0) or 0.0),
        }

    # ─── Clock ────────────────────────────────────────────────────

    def is_market_open(self) -> bool:
        return True  # Crypto is 24/7

    # ─── Stop-Loss Factory ────────────────────────────────────────
    # CCXT perpetuals handle stop-loss via exchange params, not separate orders.
    # Default BaseBroker implementation (STOP order) works fine here.

    # ─── Internal ─────────────────────────────────────────────────

    @staticmethod
    def _map_status(stat: Optional[str]) -> OrderStatus:
        if not stat:
            return OrderStatus.PENDING

        stat = stat.lower()
        if stat in ("open", "pending"):
            return OrderStatus.PENDING
        elif stat in ("closed", "filled", "settled"):
            return OrderStatus.FILLED
        elif stat == "canceled":
            return OrderStatus.CANCELLED
        elif stat == "rejected":
            return OrderStatus.REJECTED
        return OrderStatus.PENDING
