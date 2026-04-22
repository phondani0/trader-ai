from __future__ import annotations


import pandas as pd

from src.core.base_broker import BaseBroker
from src.core.models import Order, OrderType, OrderStatus, Position


class StubBroker(BaseBroker):
    def __init__(
        self,
        equity: float = 10000.0,
        tickers: dict | None = None,
        ohlcv: dict[str, pd.DataFrame] | None = None,
    ):
        self.equity = equity
        self.buying_power = equity
        self.tickers = tickers or {}
        self.ohlcv = ohlcv or {}
        self.positions: list[Position] = []
        self.orders: list[Order] = []

    @property
    def supports_short_selling(self) -> bool:
        return True

    @property
    def is_24_7_market(self) -> bool:
        return True

    @property
    def supported_order_types(self) -> list[OrderType]:
        return [OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT]

    def get_buying_power(self) -> float:
        return self.buying_power

    def get_equity(self) -> float:
        return self.equity

    def place_order(self, order: Order) -> Order:
        self.orders.append(order)
        order.status = OrderStatus.FILLED
        return order

    def cancel_order(self, order_id: str) -> bool:
        return True

    def cancel_all_orders(self) -> bool:
        return True

    def get_positions(self) -> list[Position]:
        return list(self.positions)

    def get_position(self, symbol: str):
        for position in self.positions:
            if position.symbol == symbol:
                return position
        return None

    def close_position(self, symbol: str) -> bool:
        self.positions = [position for position in self.positions if position.symbol != symbol]
        return True

    def close_all_positions(self) -> bool:
        self.positions.clear()
        return True

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 1000, since=None) -> pd.DataFrame:
        frame = self.ohlcv.get(symbol)
        if frame is None:
            return pd.DataFrame()
        if since is not None:
            since_dt = pd.to_datetime(since, unit="ms")
            frame = frame.loc[frame.index >= since_dt]
        if limit and len(frame) > limit:
            frame = frame.tail(limit)
        return frame.copy()

    def fetch_ticker(self, symbol: str) -> dict:
        return self.tickers.get(symbol, {"last": 0.0, "bid": 0.0, "ask": 0.0})

    def fetch_tickers(self, symbols=None) -> dict[str, dict]:
        if symbols:
            return {symbol: self.tickers[symbol] for symbol in symbols if symbol in self.tickers}
        return self.tickers

    def is_market_open(self) -> bool:
        return True


def build_price_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)
    return df
