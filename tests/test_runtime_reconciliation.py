from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from src.core.database import DatabaseManager
from src.core.models import MarketRegime, Position, Side, Signal, SignalType
from src.main import TradingEngine


class _RecordingSink:
    def __init__(self):
        self.items = []

    def record_trade(self, trade):
        self.items.append(trade)

    def notify_trade_closed(self, **payload):
        self.items.append(payload)

    def notify_error(self, error):
        self.items.append(error)


class _BrokerStub:
    def __init__(
        self,
        orders=None,
        positions=None,
        open_orders=None,
        is_24_7_market=True,
    ):
        self.orders = orders or []
        self.positions = positions or []
        self.open_orders = open_orders or []
        self._is_24_7_market = is_24_7_market
        self.protection_calls = []

    @property
    def uses_exchange_managed_stops(self) -> bool:
        return True

    @property
    def is_24_7_market(self) -> bool:
        return self._is_24_7_market

    def get_positions(self):
        return list(self.positions)

    def get_position(self, symbol):
        for position in self.positions:
            if position.symbol == symbol:
                return position
        return None

    def fetch_open_orders(self, symbol=None, limit=20):
        return list(self.open_orders)

    def fetch_recent_closed_orders(self, symbol=None, limit=20):
        return list(self.orders)

    def is_market_open(self):
        return True

    def set_position_protection(
        self,
        symbol,
        stop_loss=None,
        take_profit=None,
        position_side=None,
    ):
        self.protection_calls.append(
            {
                "symbol": symbol,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "position_side": position_side,
            }
        )
        return True


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

    def test_managed_position_db_round_trip(self):
        with TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=str(Path(tmp_dir) / "trader.db"))
            signal = _sample_signal()

            db.upsert_managed_position(
                {
                    "symbol": signal.symbol,
                    "order_id": "order-1",
                    "signal": signal,
                    "entry_time": datetime(2026, 4, 18, 10, 1, 0),
                    "side": Side.BUY,
                    "quantity": 1.25,
                    "entry_price": 101.5,
                }
            )

            rows = db.get_managed_positions()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], signal.symbol)
            self.assertEqual(rows[0]["strategy_name"], signal.strategy_name)
            self.assertAlmostEqual(rows[0]["quantity"], 1.25)
            self.assertIn("signal_payload_json", rows[0])

            db.delete_managed_position(signal.symbol)
            self.assertEqual(db.get_managed_positions(), [])

    def test_rehydrate_managed_positions_uses_exchange_as_source_of_truth(self):
        with TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=str(Path(tmp_dir) / "trader.db"))
            signal = _sample_signal()
            db.upsert_managed_position(
                {
                    "symbol": signal.symbol,
                    "order_id": "order-1",
                    "signal": signal,
                    "entry_time": datetime(2026, 4, 18, 10, 1, 0),
                    "side": Side.BUY,
                    "quantity": 1.0,
                    "entry_price": 100.0,
                }
            )

            engine = TradingEngine.__new__(TradingEngine)
            engine.db = db
            engine.broker = _BrokerStub(
                positions=[
                    Position(
                        symbol=signal.symbol,
                        side=Side.BUY,
                        quantity=2.0,
                        entry_price=101.5,
                        current_price=102.0,
                    )
                ]
            )
            engine._managed_positions = {}

            engine._rehydrate_managed_positions_from_exchange()

            managed = engine._managed_positions[signal.symbol]
            self.assertAlmostEqual(managed["quantity"], 2.0)
            self.assertAlmostEqual(managed["entry_price"], 101.5)
            self.assertEqual(managed["signal"].strategy_name, signal.strategy_name)
            persisted = db.get_managed_positions()[0]
            self.assertAlmostEqual(persisted["quantity"], 2.0)
            self.assertAlmostEqual(persisted["entry_price"], 101.5)

    def test_rehydrate_flags_unknown_exchange_position_without_adopting_it(self):
        with TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=str(Path(tmp_dir) / "trader.db"))
            notifier = _RecordingSink()

            engine = TradingEngine.__new__(TradingEngine)
            engine.db = db
            engine.notifier = notifier
            engine.broker = _BrokerStub(
                positions=[
                    Position(
                        symbol="ETH/USDT:USDT",
                        side=Side.BUY,
                        quantity=1.0,
                        entry_price=2100.0,
                        current_price=2150.0,
                    )
                ]
            )
            engine._managed_positions = {}

            engine._rehydrate_managed_positions_from_exchange()

            self.assertEqual(engine._managed_positions, {})
            self.assertTrue(engine._foreign_exchange_state_detected)
            self.assertEqual(len(notifier.items), 1)
            self.assertIn("Foreign exchange state detected", notifier.items[0])

    def test_adopt_exchange_position_persists_and_protects_legacy_position(self):
        with TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=str(Path(tmp_dir) / "trader.db"))
            notifier = _RecordingSink()
            broker = _BrokerStub(
                positions=[
                    Position(
                        symbol="BTC/USDT:USDT",
                        side=Side.BUY,
                        quantity=1.25,
                        entry_price=101.5,
                        current_price=103.0,
                    )
                ]
            )

            engine = TradingEngine.__new__(TradingEngine)
            engine.db = db
            engine.notifier = notifier
            engine.broker = broker
            engine._managed_positions = {}
            engine._foreign_exchange_state_detected = True
            engine._foreign_exchange_state_signature = "stale"

            managed = engine.adopt_exchange_position(
                symbol="BTC/USDT:USDT",
                strategy_name="vwap_reversion",
                stop_loss=95.0,
                take_profit=110.0,
                reason="manual adoption",
            )

            self.assertEqual(managed["signal"].strategy_name, "vwap_reversion")
            self.assertEqual(managed["signal"].metadata["adopted"], True)
            self.assertAlmostEqual(managed["quantity"], 1.25)
            self.assertEqual(len(broker.protection_calls), 1)
            protection = broker.protection_calls[0]
            self.assertEqual(protection["symbol"], "BTC/USDT:USDT")
            self.assertEqual(protection["stop_loss"], 95.0)
            self.assertEqual(protection["take_profit"], 110.0)
            self.assertFalse(engine._foreign_exchange_state_detected)
            persisted = db.get_managed_positions()[0]
            self.assertEqual(persisted["symbol"], "BTC/USDT:USDT")
            self.assertAlmostEqual(persisted["take_profit"], 110.0)

    def test_adopt_exchange_position_rejects_missing_exchange_position(self):
        with TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=str(Path(tmp_dir) / "trader.db"))

            engine = TradingEngine.__new__(TradingEngine)
            engine.db = db
            engine.notifier = _RecordingSink()
            engine.broker = _BrokerStub(positions=[])
            engine._managed_positions = {}

            with self.assertRaisesRegex(ValueError, "No open exchange position"):
                engine.adopt_exchange_position(
                    symbol="BTC/USDT:USDT",
                    strategy_name="vwap_reversion",
                    stop_loss=95.0,
                    take_profit=110.0,
                )

    def test_rehydrate_flags_unknown_open_order_on_bot_only_account(self):
        with TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=str(Path(tmp_dir) / "trader.db"))
            notifier = _RecordingSink()

            engine = TradingEngine.__new__(TradingEngine)
            engine.db = db
            engine.notifier = notifier
            engine.broker = _BrokerStub(
                open_orders=[
                    {
                        "id": "manual-order-1",
                        "symbol": "SOL/USDT:USDT",
                        "side": "buy",
                        "price": 150.0,
                    }
                ]
            )
            engine._managed_positions = {}

            engine._rehydrate_managed_positions_from_exchange()

            self.assertEqual(engine._managed_positions, {})
            self.assertTrue(engine._foreign_exchange_state_detected)
            self.assertEqual(len(notifier.items), 1)
            self.assertIn("manual-order-1", notifier.items[0])

    def test_known_reduce_only_order_for_managed_symbol_is_not_flagged_foreign(self):
        with TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=str(Path(tmp_dir) / "trader.db"))
            signal = _sample_signal()
            db.upsert_managed_position(
                {
                    "symbol": signal.symbol,
                    "order_id": "entry-1",
                    "signal": signal,
                    "entry_time": datetime(2026, 4, 18, 10, 1, 0),
                    "side": Side.BUY,
                    "quantity": 1.0,
                    "entry_price": 100.0,
                }
            )

            notifier = _RecordingSink()
            engine = TradingEngine.__new__(TradingEngine)
            engine.db = db
            engine.notifier = notifier
            engine.broker = _BrokerStub(
                positions=[
                    Position(
                        symbol=signal.symbol,
                        side=Side.BUY,
                        quantity=1.0,
                        entry_price=100.0,
                        current_price=102.0,
                    )
                ],
                open_orders=[
                    {
                        "id": "protective-1",
                        "symbol": signal.symbol,
                        "side": "sell",
                        "reduceOnly": True,
                        "triggerPrice": 95.0,
                    }
                ],
            )
            engine._managed_positions = {}

            engine._rehydrate_managed_positions_from_exchange()

            self.assertFalse(engine._foreign_exchange_state_detected)
            self.assertEqual(len(notifier.items), 0)

    def test_rehydrate_records_exchange_side_close_for_missing_exchange_position(self):
        with TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=str(Path(tmp_dir) / "trader.db"))
            signal = _sample_signal()
            db.upsert_managed_position(
                {
                    "symbol": signal.symbol,
                    "order_id": "order-1",
                    "signal": signal,
                    "entry_time": datetime(2026, 4, 18, 10, 1, 0),
                    "side": Side.BUY,
                    "quantity": 1.0,
                    "entry_price": 100.0,
                }
            )

            risk_sink = _RecordingSink()
            notifier = _RecordingSink()
            engine = TradingEngine.__new__(TradingEngine)
            engine.db = db
            engine.risk_manager = risk_sink
            engine.notifier = notifier
            engine.broker = _BrokerStub(
                orders=[
                    {
                        "side": "sell",
                        "average": 96.0,
                        "filled": 1.0,
                        "datetime": "2026-04-18T10:05:00Z",
                        "info": {"reduceOnly": True},
                    }
                ],
                positions=[],
            )
            engine._managed_positions = {}

            engine._rehydrate_managed_positions_from_exchange()

            self.assertEqual(engine._managed_positions, {})
            self.assertEqual(db.get_managed_positions(), [])
            self.assertEqual(len(risk_sink.items), 1)
            self.assertEqual(len(notifier.items), 1)

    def test_sync_managed_positions_updates_manual_size_reduction(self):
        with TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=str(Path(tmp_dir) / "trader.db"))
            signal = _sample_signal()
            db.upsert_managed_position(
                {
                    "symbol": signal.symbol,
                    "order_id": "order-1",
                    "signal": signal,
                    "entry_time": datetime(2026, 4, 18, 10, 1, 0),
                    "side": Side.BUY,
                    "quantity": 2.0,
                    "entry_price": 100.0,
                }
            )

            engine = TradingEngine.__new__(TradingEngine)
            engine.db = db
            engine.broker = _BrokerStub()
            engine.notifier = _RecordingSink()
            engine._managed_positions = {
                signal.symbol: {
                    "order_id": "order-1",
                    "signal": signal,
                    "entry_time": datetime(2026, 4, 18, 10, 1, 0),
                    "side": Side.BUY,
                    "quantity": 2.0,
                    "entry_price": 100.0,
                }
            }

            engine._sync_managed_positions_with_exchange(
                [
                    Position(
                        symbol=signal.symbol,
                        side=Side.BUY,
                        quantity=1.25,
                        entry_price=100.0,
                        current_price=99.0,
                    )
                ]
            )

            self.assertAlmostEqual(engine._managed_positions[signal.symbol]["quantity"], 1.25)
            persisted = db.get_managed_positions()[0]
            self.assertAlmostEqual(persisted["quantity"], 1.25)
            self.assertEqual(len(engine.notifier.items), 1)
            self.assertIn("drifted from bot intent", engine.notifier.items[0])

    def test_shutdown_skips_square_off_for_crypto_restart(self):
        engine = TradingEngine.__new__(TradingEngine)
        engine._running = True
        engine.broker = _BrokerStub(is_24_7_market=True)
        engine.scheduler = SimpleNamespace(
            scheduler=SimpleNamespace(running=False),
            stop=lambda: None,
        )
        square_off_calls = []
        engine.on_square_off = lambda: square_off_calls.append("called")

        engine.shutdown()

        self.assertEqual(square_off_calls, [])


if __name__ == "__main__":
    unittest.main()
