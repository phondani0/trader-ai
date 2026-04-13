from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from src.core.models import Side
from src.main import TradingEngine


class _RecordingSink:
    def __init__(self):
        self.items = []

    def record_trade(self, trade):
        self.items.append(trade)

    def notify_trade_closed(self, **payload):
        self.items.append(payload)


class _BrokerStub:
    def __init__(self, orders):
        self.orders = orders

    @property
    def uses_exchange_managed_stops(self) -> bool:
        return True

    def fetch_recent_closed_orders(self, symbol=None, limit=20):
        return list(self.orders)


class RuntimeReconciliationTest(unittest.TestCase):
    def test_match_closing_order_prefers_latest_valid_opposite_side(self):
        engine = TradingEngine.__new__(TradingEngine)
        managed = {
            "side": Side.BUY,
            "entry_time": datetime(2026, 4, 12, 22, 5, 38),
        }
        closed_orders = [
            {"side": "sell", "datetime": "2026-04-12T22:00:00Z", "average": 90.0},
            {"side": "buy", "datetime": "2026-04-12T22:10:00Z", "average": 105.0},
            {"side": "sell", "datetime": "2026-04-12T22:15:00Z", "average": 110.0},
        ]

        match = engine._match_closing_order(managed, closed_orders)

        self.assertIsNotNone(match)
        self.assertEqual(float(match["average"]), 110.0)

    def test_infer_exchange_close_reason_detects_stop_loss(self):
        reason = TradingEngine._infer_exchange_close_reason(
            {
                "info": {
                    "stopOrderType": "StopLoss",
                    "createType": "CreateByStopLoss",
                }
            }
        )

        self.assertEqual(reason, "Exchange stop-loss triggered")

    def test_record_exchange_side_close_records_trade_and_clears_tracking(self):
        engine = TradingEngine.__new__(TradingEngine)
        risk_sink = _RecordingSink()
        db_sink = _RecordingSink()
        notifier = _RecordingSink()

        engine.broker = _BrokerStub(
            [
                {
                    "side": "sell",
                    "average": 95.0,
                    "filled": 2.0,
                    "datetime": "2026-04-12T22:10:00Z",
                    "info": {
                        "stopOrderType": "StopLoss",
                        "createType": "CreateByStopLoss",
                    },
                }
            ]
        )
        engine.risk_manager = risk_sink
        engine.db = db_sink
        engine.notifier = notifier
        engine._managed_positions = {
            "BTC/USDT:USDT": {
                "side": Side.BUY,
                "entry_time": datetime(2026, 4, 12, 22, 5, 38),
                "quantity": 2.0,
                "entry_price": 100.0,
                "signal": SimpleNamespace(
                    strategy_name="vwap_reversion",
                    entry_price=100.0,
                ),
            }
        }

        engine._record_exchange_side_close("BTC/USDT:USDT")

        self.assertNotIn("BTC/USDT:USDT", engine._managed_positions)
        self.assertEqual(len(risk_sink.items), 1)
        trade = risk_sink.items[0]
        self.assertEqual(trade.reason_exit, "Exchange stop-loss triggered")
        self.assertAlmostEqual(trade.exit_price, 95.0)
        self.assertAlmostEqual(trade.quantity, 2.0)
        self.assertEqual(len(db_sink.items), 1)
        self.assertEqual(len(notifier.items), 1)


if __name__ == "__main__":
    unittest.main()
