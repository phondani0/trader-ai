from __future__ import annotations

import unittest
from datetime import datetime

from src.core.broker import CCXTBroker
from src.core.models import (
    MarketRegime,
    Order,
    OrderType,
    Side,
    Signal,
    SignalType,
)


class _ExchangeStub:
    def __init__(self):
        self.create_order_calls = []
        self.set_leverage_calls = []
        self.trading_stop_calls = []
        self.fetch_positions_result = []
        self.market_max_qty = None

    def set_leverage(self, leverage, symbol):
        self.set_leverage_calls.append((leverage, symbol))

    def create_order(self, symbol, type, side, amount, price, params):
        self.create_order_calls.append(
            {
                "symbol": symbol,
                "type": type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": params,
            }
        )
        return {
            "id": "entry-1",
            "status": "closed",
            "filled": amount,
            "average": 101.5,
        }

    def market(self, symbol):
        market = {
            "id": "BTCUSDT",
            "linear": True,
            "inverse": False,
            "limits": {"amount": {}},
        }
        if self.market_max_qty is not None:
            market["limits"]["amount"]["max"] = self.market_max_qty
        return market

    def amount_to_precision(self, symbol, amount):
        return f"{float(amount):.4f}"

    def fetch_positions(self, symbols=None):
        return list(self.fetch_positions_result)

    def private_post_v5_position_trading_stop(self, payload):
        self.trading_stop_calls.append(payload)
        return {"retCode": 0, "retMsg": "OK"}


def _sample_signal() -> Signal:
    return Signal(
        timestamp=datetime(2026, 4, 18, 10, 0, 0),
        symbol="BTC/USDT:USDT",
        strategy_name="vwap_reversion",
        signal_type=SignalType.BUY,
        side=Side.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=0.8,
        regime=MarketRegime.SIDEWAYS_LOW_VOL,
        reason="test fixture",
        metadata={"fixture": True},
    )


class CCXTBrokerProtectionTest(unittest.TestCase):
    def test_place_order_attaches_take_profit_and_stop_loss(self):
        broker = CCXTBroker.__new__(CCXTBroker)
        broker.exchange = _ExchangeStub()
        broker._leverage = 5
        broker.exchange_id = "bybit"

        signal = _sample_signal()
        order = Order(
            symbol=signal.symbol,
            side=Side.BUY,
            quantity=1.25,
            order_type=OrderType.MARKET,
            strategy_name=signal.strategy_name,
            signal=signal,
        )

        result = broker.place_order(order)

        self.assertEqual(result.id, "entry-1")
        self.assertEqual(len(broker.exchange.create_order_calls), 1)
        call = broker.exchange.create_order_calls[0]
        self.assertEqual(call["symbol"], signal.symbol)
        self.assertEqual(call["type"], "market")
        self.assertEqual(
            call["params"]["stopLoss"],
            {"triggerPrice": signal.stop_loss},
        )
        self.assertEqual(
            call["params"]["takeProfit"],
            {"triggerPrice": signal.take_profit},
        )

    def test_set_position_protection_updates_existing_bybit_position(self):
        broker = CCXTBroker.__new__(CCXTBroker)
        broker.exchange = _ExchangeStub()
        broker._leverage = 5
        broker.exchange_id = "bybit"
        broker.exchange.fetch_positions_result = [
            {
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "contracts": 1.0,
                "info": {"positionIdx": "0"},
            }
        ]

        success = broker.set_position_protection(
            symbol="BTC/USDT:USDT",
            stop_loss=95.0,
            take_profit=110.0,
            position_side=Side.BUY,
        )

        self.assertTrue(success)
        self.assertEqual(len(broker.exchange.trading_stop_calls), 1)
        payload = broker.exchange.trading_stop_calls[0]
        self.assertEqual(payload["category"], "linear")
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(payload["positionIdx"], 0)
        self.assertEqual(payload["stopLoss"], "95.0")
        self.assertEqual(payload["takeProfit"], "110.0")

    def test_place_order_caps_quantity_to_exchange_max(self):
        broker = CCXTBroker.__new__(CCXTBroker)
        broker.exchange = _ExchangeStub()
        broker.exchange.market_max_qty = 100.0
        broker._leverage = 5
        broker.exchange_id = "bybit"

        signal = _sample_signal()
        order = Order(
            symbol=signal.symbol,
            side=Side.BUY,
            quantity=125.6789,
            order_type=OrderType.MARKET,
            strategy_name=signal.strategy_name,
            signal=signal,
        )

        result = broker.place_order(order)

        self.assertEqual(result.id, "entry-1")
        self.assertEqual(len(broker.exchange.create_order_calls), 1)
        call = broker.exchange.create_order_calls[0]
        self.assertEqual(call["amount"], 100.0)
        self.assertEqual(order.quantity, 100.0)


if __name__ == "__main__":
    unittest.main()
