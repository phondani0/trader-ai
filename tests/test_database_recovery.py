"""Tests for recovery database schema and CRUD methods."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from trader_ai.core.database import DatabaseManager


class RecoverySchemaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = DatabaseManager(db_path=self.tmp.name)

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def _table_exists(self, table: str) -> bool:
        with self.db._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            return cursor.fetchone() is not None

    def _table_columns(self, table: str) -> set[str]:
        with self.db._get_connection() as conn:
            cursor = conn.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cursor.fetchall()}

    def test_strategy_positions_table_exists(self):
        self.assertTrue(self._table_exists("strategy_positions"))

    def test_strategy_orders_table_exists(self):
        self.assertTrue(self._table_exists("strategy_orders"))

    def test_reconciliation_runs_table_exists(self):
        self.assertTrue(self._table_exists("reconciliation_runs"))

    def test_strategy_positions_has_required_columns(self):
        cols = self._table_columns("strategy_positions")
        required = {
            "strategy_name", "symbol", "position_id", "instrument_id",
            "trade_id", "side", "quantity", "exchange_entry_price",
            "stop_price", "best_price", "trailing_active",
            "bars_in_position", "status", "original_run_id", "last_run_id",
        }
        self.assertTrue(required.issubset(cols), f"Missing columns: {required - cols}")

    def test_wal_mode_enabled(self):
        with self.db._get_connection() as conn:
            result = conn.execute("PRAGMA journal_mode").fetchone()
            self.assertEqual(result[0], "wal")

    def test_upsert_strategy_position_idempotent(self):
        base = {
            "strategy_name": "test_strat",
            "symbol": "BTC/USDT:USDT",
            "position_id": "P-123",
            "instrument_id": "BTCUSDT-LINEAR.BYBIT",
            "trade_id": "T-abc",
            "side": "LONG",
            "quantity": 0.1,
            "exchange_entry_price": 50000.0,
            "status": "open",
        }
        self.db.upsert_strategy_position(base)
        self.db.upsert_strategy_position({**base, "quantity": 0.05, "stop_price": 49000.0})

        rows = self.db.get_open_strategy_positions()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["quantity"], 0.05)
        self.assertAlmostEqual(rows[0]["stop_price"], 49000.0)

    def test_mark_position_closed(self):
        self.db.upsert_strategy_position({
            "strategy_name": "test_strat",
            "symbol": "BTC/USDT:USDT",
            "position_id": "P-close-test",
            "instrument_id": "BTCUSDT-LINEAR.BYBIT",
            "trade_id": "T-close",
            "side": "LONG",
            "quantity": 0.1,
            "exchange_entry_price": 50000.0,
            "status": "open",
        })
        self.db.mark_position_closed("test_strat", "BTC/USDT:USDT", "P-close-test", "stop_hit")

        rows = self.db.get_open_strategy_positions()
        self.assertEqual(len(rows), 0)

    def test_mark_position_externally_closed(self):
        self.db.upsert_strategy_position({
            "strategy_name": "test_strat",
            "symbol": "ETH/USDT:USDT",
            "position_id": "P-ext",
            "instrument_id": "ETHUSDT-LINEAR.BYBIT",
            "trade_id": "T-ext",
            "side": "SHORT",
            "quantity": 1.0,
            "exchange_entry_price": 3000.0,
            "status": "open",
        })
        self.db.mark_position_externally_closed("test_strat", "ETH/USDT:USDT", "P-ext")

        rows = self.db.get_open_strategy_positions()
        self.assertEqual(len(rows), 0)

        with self.db._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM strategy_positions WHERE position_id = 'P-ext'"
            )
            row = dict(cursor.fetchone())
            self.assertEqual(row["status"], "externally_closed")
            self.assertIsNotNone(row["closed_at"])

    def test_reconciliation_run_lifecycle(self):
        self.db.create_reconciliation_run("run-001", "startup")

        with self.db._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute(
                "SELECT * FROM reconciliation_runs WHERE run_id = 'run-001'"
            ).fetchone())
            self.assertEqual(row["result"], "running")
            self.assertIsNone(row["finished_at"])

        self.db.finish_reconciliation_run("run-001", "clean", {
            "exchange_open": 0,
            "db_open": 0,
            "recovered": 0,
            "blocked": 0,
            "failed": 0,
        })

        with self.db._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute(
                "SELECT * FROM reconciliation_runs WHERE run_id = 'run-001'"
            ).fetchone())
            self.assertEqual(row["result"], "clean")
            self.assertIsNotNone(row["finished_at"])

    def test_strategy_state_json_round_trip(self):
        state_json = {"cooldown_remaining": 5, "volumes": [100.0, 200.0]}
        self.db.upsert_strategy_position({
            "strategy_name": "vol_spike",
            "symbol": "NEAR/USDT:USDT",
            "position_id": "P-json",
            "instrument_id": "NEARUSDT-LINEAR.BYBIT",
            "trade_id": "T-json",
            "side": "LONG",
            "quantity": 10.0,
            "exchange_entry_price": 5.0,
            "status": "open",
            "strategy_state_json": state_json,
        })

        rows = self.db.get_open_strategy_positions()
        self.assertEqual(len(rows), 1)
        parsed = json.loads(rows[0]["strategy_state_json"])
        self.assertEqual(parsed["cooldown_remaining"], 5)

    def test_upsert_strategy_order(self):
        self.db.upsert_strategy_order({
            "client_order_id": "O-abc",
            "trade_id": "T-abc",
            "strategy_name": "test_strat",
            "symbol": "BTC/USDT:USDT",
            "side": "BUY",
            "status": "submitted",
        })
        self.db.upsert_strategy_order({
            "client_order_id": "O-abc",
            "venue_order_id": "V-xyz",
            "trade_id": "T-abc",
            "strategy_name": "test_strat",
            "symbol": "BTC/USDT:USDT",
            "side": "BUY",
            "status": "filled",
        })

        with self.db._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute(
                "SELECT * FROM strategy_orders WHERE client_order_id = 'O-abc'"
            ).fetchone())
            self.assertEqual(row["status"], "filled")
            self.assertEqual(row["venue_order_id"], "V-xyz")


class ManagedPositionsMigrationTest(unittest.TestCase):
    def test_migration_copies_rows(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        try:
            conn = sqlite3.connect(tmp.name)
            conn.execute("""
                CREATE TABLE managed_positions (
                    symbol TEXT PRIMARY KEY,
                    order_id TEXT,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_time TIMESTAMP,
                    strategy_name TEXT,
                    stop_loss REAL,
                    take_profit REAL,
                    signal_payload_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO managed_positions (symbol, side, quantity, entry_price, strategy_name) "
                "VALUES ('BTC/USDT:USDT', 'LONG', 0.1, 50000.0, 'test_strat')"
            )
            conn.commit()
            conn.close()

            db = DatabaseManager(db_path=tmp.name)
            rows = db.get_open_strategy_positions()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["position_id"].startswith("migrated-"))
            self.assertEqual(rows[0]["status"], "recovered")
            self.assertEqual(rows[0]["recovery_reason"], "schema_migration")
        finally:
            Path(tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
