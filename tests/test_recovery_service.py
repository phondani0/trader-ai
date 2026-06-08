"""Tests for the PositionRecoveryService decision matrix."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from trader_ai.core.database import DatabaseManager
from trader_ai.execution.recovery import (
    ExchangePositionSnapshot,
    PositionRecoveryService,
    StrategyPositionState,
    strategy_position_state_from_row,
)


class RecoveryModelsTest(unittest.TestCase):
    def test_exchange_snapshot_creation(self):
        snap = ExchangePositionSnapshot(
            instrument_id="BTCUSDT-LINEAR.BYBIT",
            symbol="BTC/USDT:USDT",
            side="LONG",
            quantity=0.1,
            avg_entry_price=50000.0,
        )
        self.assertEqual(snap.symbol, "BTC/USDT:USDT")
        self.assertEqual(snap.quantity, 0.1)

    def test_strategy_position_state_defaults(self):
        state = StrategyPositionState(
            strategy_name="test",
            symbol="BTC/USDT:USDT",
            instrument_id="BTCUSDT-LINEAR.BYBIT",
            position_id="P-1",
            trade_id="T-1",
            side="LONG",
            quantity=0.1,
            entry_price=50000.0,
        )
        self.assertFalse(state.trailing_active)
        self.assertEqual(state.bars_in_position, 0)
        self.assertIsNone(state.strategy_state_json)


class PositionRecoveryServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = DatabaseManager(db_path=self.tmp.name)
        self.service = PositionRecoveryService(self.db, run_id="test-run-001")

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def _make_exchange(self, symbol: str, side: str = "LONG", qty: float = 0.1) -> ExchangePositionSnapshot:
        return ExchangePositionSnapshot(
            instrument_id=f"{symbol.split('/')[0]}USDT-LINEAR.BYBIT",
            symbol=symbol,
            side=side,
            quantity=qty,
            avg_entry_price=50000.0,
        )

    def _make_db(self, symbol: str, side: str = "LONG", qty: float = 0.1) -> StrategyPositionState:
        return StrategyPositionState(
            strategy_name="test_strat",
            symbol=symbol,
            instrument_id=f"{symbol.split('/')[0]}USDT-LINEAR.BYBIT",
            position_id="P-test",
            trade_id="T-test",
            side=side,
            quantity=qty,
            entry_price=50000.0,
            entry_atr=500.0,
            stop_price=49000.0,
            best_price=50500.0,
        )

    def test_clean_reconciliation_no_positions(self):
        decisions = self.service.reconcile([], [])
        self.assertEqual(len(decisions), 0)
        self.assertTrue(self.service.recovery_complete)
        self.assertEqual(len(self.service.blocked_symbols), 0)

    def test_recover_matching_position(self):
        exchange = [self._make_exchange("BTC/USDT:USDT")]
        db = [self._make_db("BTC/USDT:USDT")]
        decisions = self.service.reconcile(exchange, db)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "recover")
        self.assertEqual(decisions[0].reason, "matching_position")
        self.assertNotIn("BTC/USDT:USDT", self.service.blocked_symbols)

    def test_recover_with_buy_sell_aliases(self):
        exchange = [self._make_exchange("BTC/USDT:USDT", side="BUY")]
        db = [self._make_db("BTC/USDT:USDT", side="LONG")]
        decisions = self.service.reconcile(exchange, db)

        self.assertEqual(decisions[0].action, "recover")

    def test_block_unmanaged_exchange_position(self):
        exchange = [self._make_exchange("ETH/USDT:USDT")]
        decisions = self.service.reconcile(exchange, [])

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "block")
        self.assertEqual(decisions[0].reason, "unmanaged_exchange_position")
        self.assertIn("ETH/USDT:USDT", self.service.blocked_symbols)

    def test_externally_closed_db_open_exchange_flat(self):
        db = [self._make_db("NEAR/USDT:USDT")]
        decisions = self.service.reconcile([], db)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "externally_closed")
        self.assertEqual(decisions[0].reason, "exchange_flat_db_open")

    def test_fail_side_mismatch(self):
        exchange = [self._make_exchange("BTC/USDT:USDT", side="LONG")]
        db = [self._make_db("BTC/USDT:USDT", side="SHORT")]
        decisions = self.service.reconcile(exchange, db)

        self.assertEqual(decisions[0].action, "fail")
        self.assertEqual(decisions[0].reason, "side_mismatch")
        self.assertIn("BTC/USDT:USDT", self.service.blocked_symbols)

    def test_block_quantity_increased_externally(self):
        exchange = [self._make_exchange("BTC/USDT:USDT", qty=0.5)]
        db = [self._make_db("BTC/USDT:USDT", qty=0.1)]
        decisions = self.service.reconcile(exchange, db)

        self.assertEqual(decisions[0].action, "block")
        self.assertEqual(decisions[0].reason, "quantity_increased_external")
        self.assertIn("BTC/USDT:USDT", self.service.blocked_symbols)

    def test_recover_partial_close_quantity_decreased(self):
        exchange = [self._make_exchange("BTC/USDT:USDT", qty=0.05)]
        db = [self._make_db("BTC/USDT:USDT", qty=0.1)]
        decisions = self.service.reconcile(exchange, db)

        self.assertEqual(decisions[0].action, "recover")
        self.assertNotIn("BTC/USDT:USDT", self.service.blocked_symbols)

    def test_is_entry_allowed_before_reconciliation(self):
        self.assertFalse(self.service.is_entry_allowed("BTC/USDT:USDT"))

    def test_is_entry_allowed_after_clean_reconciliation(self):
        self.service.reconcile([], [])
        self.assertTrue(self.service.is_entry_allowed("BTC/USDT:USDT"))

    def test_is_entry_blocked_for_unmanaged_exchange_symbol(self):
        exchange = [self._make_exchange("ETH/USDT:USDT")]
        self.service.reconcile(exchange, [])
        self.assertFalse(self.service.is_entry_allowed("ETH/USDT:USDT"))
        self.assertTrue(self.service.is_entry_allowed("BTC/USDT:USDT"))

    def test_persist_state(self):
        self.service.reconcile([], [])
        state = StrategyPositionState(
            strategy_name="test_strat",
            symbol="BTC/USDT:USDT",
            instrument_id="BTCUSDT-LINEAR.BYBIT",
            position_id="P-persist",
            trade_id="T-persist",
            side="LONG",
            quantity=0.1,
            entry_price=50000.0,
            stop_price=49000.0,
            best_price=51000.0,
            trailing_active=True,
            bars_in_position=5,
            strategy_state_json={"cooldown_remaining": 3},
        )
        self.service.persist_state(state)

        rows = self.db.get_open_strategy_positions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_name"], "test_strat")
        self.assertAlmostEqual(rows[0]["stop_price"], 49000.0)
        self.assertEqual(rows[0]["trailing_active"], 1)

    def test_reconciliation_run_audit_trail(self):
        exchange = [self._make_exchange("BTC/USDT:USDT")]
        db = [self._make_db("BTC/USDT:USDT")]
        self.service.reconcile(exchange, db)

        with self.db._get_connection() as conn:
            import sqlite3
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute(
                "SELECT * FROM reconciliation_runs WHERE run_id = 'test-run-001'"
            ).fetchone())
            self.assertEqual(row["result"], "recovered")
            self.assertEqual(row["recovered_count"], 1)
            self.assertIsNotNone(row["finished_at"])

    def test_mixed_scenario(self):
        exchange = [
            self._make_exchange("BTC/USDT:USDT"),
            self._make_exchange("ETH/USDT:USDT"),
        ]
        db = [
            self._make_db("BTC/USDT:USDT"),
            self._make_db("NEAR/USDT:USDT"),
        ]
        decisions = self.service.reconcile(exchange, db)

        actions = {d.symbol: d.action for d in decisions}
        self.assertEqual(actions["BTC/USDT:USDT"], "recover")
        self.assertEqual(actions["ETH/USDT:USDT"], "block")
        self.assertEqual(actions["NEAR/USDT:USDT"], "externally_closed")

        self.assertIn("ETH/USDT:USDT", self.service.blocked_symbols)
        self.assertNotIn("BTC/USDT:USDT", self.service.blocked_symbols)

    def test_strategy_position_state_from_database_row(self):
        self.service.persist_state(
            StrategyPositionState(
                strategy_name="test_strat",
                symbol="BTC/USDT:USDT",
                instrument_id="BTCUSDT-LINEAR.BYBIT",
                position_id="P-row",
                trade_id="T-row",
                side="LONG",
                quantity=0.2,
                entry_price=50000.0,
                entry_atr=100.0,
                stop_price=49700.0,
                best_price=50100.0,
                trailing_active=True,
                bars_in_position=2,
                strategy_state_json={"cooldown_remaining": 1},
            )
        )

        row = self.db.get_open_strategy_positions()[0]
        state = strategy_position_state_from_row(row)

        self.assertEqual(state.strategy_name, "test_strat")
        self.assertEqual(state.symbol, "BTC/USDT:USDT")
        self.assertEqual(state.quantity, 0.2)
        self.assertEqual(state.entry_price, 50000.0)
        self.assertEqual(state.strategy_state_json, {"cooldown_remaining": 1})


if __name__ == "__main__":
    unittest.main()
