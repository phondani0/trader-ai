"""
Database Manager.
Handles persistence for trades, discoveries, performance metrics,
and strategy position recovery state.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from dataclasses import dataclass

@dataclass
class Trade:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    pnl_pct: float
    strategy_name: str
    regime: Any
    reason_entry: str
    reason_exit: str
class DatabaseManager:
    """
    SQLite database manager for persistent trading records.
    """

    def __init__(self, db_path: str = "data/trader.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Discovery Journal Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS discovery_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    discovery_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen_time TIMESTAMP,
                    entry_price REAL,
                    entry_volume_24h REAL,
                    discovery_score REAL,
                    discovery_source TEXT,
                    scanner_profile TEXT,
                    direction TEXT,
                    liquidity_score REAL,
                    burst_score REAL,
                    breakout_score REAL,
                    regime TEXT,
                    discovery_change_pct REAL,
                    discovery_count INTEGER DEFAULT 1,
                    scanner_metadata_json TEXT,
                    price_1h REAL,
                    price_4h REAL,
                    price_24h REAL,
                    max_price REAL,
                    min_price REAL,
                    status TEXT DEFAULT 'pending'
                )
                """
            )

            # 2. Trades Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT,
                    quantity REAL,
                    entry_price REAL,
                    exit_price REAL,
                    entry_time TIMESTAMP,
                    exit_time TIMESTAMP,
                    pnl REAL,
                    pnl_pct REAL,
                    strategy_name TEXT,
                    regime TEXT,
                    reason_entry TEXT,
                    reason_exit TEXT
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS managed_positions (
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
                """
            )

            # 3. Passive parameter suggestions
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS parameter_suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    source TEXT NOT NULL,
                    parameter_name TEXT NOT NULL,
                    current_value REAL,
                    suggested_value REAL,
                    confidence REAL,
                    rationale TEXT,
                    metadata_json TEXT
                )
                """
            )

            # 4. Strategy position recovery ledger
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_positions (
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    position_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    venue TEXT NOT NULL DEFAULT 'BYBIT',
                    trade_id TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('LONG', 'SHORT', 'BUY', 'SELL')),
                    quantity REAL NOT NULL CHECK(quantity >= 0),
                    exchange_entry_price REAL NOT NULL CHECK(exchange_entry_price >= 0),
                    strategy_entry_price REAL CHECK(strategy_entry_price >= 0),
                    entry_atr REAL CHECK(entry_atr >= 0),
                    stop_price REAL CHECK(stop_price >= 0),
                    best_price REAL CHECK(best_price >= 0),
                    trailing_active INTEGER NOT NULL DEFAULT 0 CHECK(trailing_active IN (0, 1)),
                    bars_in_position INTEGER NOT NULL DEFAULT 0 CHECK(bars_in_position >= 0),
                    entry_time TEXT,
                    last_bar_time TEXT,
                    last_exchange_sync_time TEXT,
                    status TEXT NOT NULL CHECK(status IN (
                        'opening', 'open', 'recovered', 'adopted', 'external',
                        'blocked', 'closing', 'closed', 'externally_closed',
                        'reconciliation_failed'
                    )),
                    entry_reason TEXT,
                    exit_reason TEXT,
                    recovery_reason TEXT,
                    discovery_journal_id INTEGER,
                    signal_payload_json TEXT,
                    exchange_snapshot_json TEXT,
                    strategy_state_json TEXT,
                    original_run_id TEXT,
                    last_run_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    closed_at TEXT,
                    PRIMARY KEY (strategy_name, symbol, position_id)
                )
                """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_positions_status
                ON strategy_positions(status)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_positions_symbol_status
                ON strategy_positions(symbol, status)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_positions_trade_id
                ON strategy_positions(trade_id)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_positions_updated_at
                ON strategy_positions(updated_at)
                """
            )

            # 5. Strategy order correlation
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_orders (
                    client_order_id TEXT PRIMARY KEY,
                    venue_order_id TEXT,
                    position_id TEXT,
                    trade_id TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT,
                    quantity REAL CHECK(quantity >= 0),
                    status TEXT NOT NULL,
                    reduce_only INTEGER NOT NULL DEFAULT 0 CHECK(reduce_only IN (0, 1)),
                    submitted_at TEXT,
                    filled_at TEXT,
                    closed_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    raw_event_json TEXT
                )
                """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_orders_trade_id
                ON strategy_orders(trade_id)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_orders_position_id
                ON strategy_orders(position_id)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_orders_symbol_status
                ON strategy_orders(symbol, status)
                """
            )

            # 6. Reconciliation audit log
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS reconciliation_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    trigger TEXT NOT NULL CHECK(trigger IN ('startup', 'reconnect', 'manual', 'test')),
                    result TEXT NOT NULL CHECK(result IN ('running', 'clean', 'recovered', 'blocked', 'failed')),
                    exchange_open_count INTEGER NOT NULL DEFAULT 0,
                    db_open_count INTEGER NOT NULL DEFAULT 0,
                    recovered_count INTEGER NOT NULL DEFAULT 0,
                    adopted_count INTEGER NOT NULL DEFAULT 0,
                    externally_closed_count INTEGER NOT NULL DEFAULT 0,
                    blocked_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT
                )
                """
            )

            self._ensure_column(
                cursor, "discovery_journal", "last_seen_time", "TIMESTAMP"
            )
            self._ensure_column(
                cursor, "discovery_journal", "discovery_change_pct", "REAL"
            )
            self._ensure_column(cursor, "discovery_journal", "discovery_source", "TEXT")
            self._ensure_column(cursor, "discovery_journal", "scanner_profile", "TEXT")
            self._ensure_column(cursor, "discovery_journal", "direction", "TEXT")
            self._ensure_column(cursor, "discovery_journal", "liquidity_score", "REAL")
            self._ensure_column(cursor, "discovery_journal", "burst_score", "REAL")
            self._ensure_column(cursor, "discovery_journal", "breakout_score", "REAL")
            self._ensure_column(
                cursor,
                "discovery_journal",
                "discovery_count",
                "INTEGER DEFAULT 1",
            )
            self._ensure_column(
                cursor,
                "discovery_journal",
                "scanner_metadata_json",
                "TEXT",
            )
            self._ensure_column(cursor, "discovery_journal", "min_price", "REAL")
            self._ensure_column(cursor, "trades", "regime", "TEXT")
            self._ensure_column(cursor, "trades", "reason_entry", "TEXT")

            self._migrate_managed_positions(cursor)
            conn.commit()
        logger.info(f"💾 Database initialized at {self.db_path}")

    @staticmethod
    def _ensure_column(
        cursor: sqlite3.Cursor, table: str, column: str, definition: str
    ):
        cursor.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cursor.fetchall()}
        if column not in columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _migrate_managed_positions(cursor: sqlite3.Cursor) -> None:
        """One-time migration: copy managed_positions rows to strategy_positions."""
        cursor.execute("PRAGMA table_info(managed_positions)")
        if not cursor.fetchall():
            return

        cursor.execute(
            """
            SELECT symbol, order_id, side, quantity, entry_price,
                   entry_time, strategy_name, stop_loss, updated_at
            FROM managed_positions
            """
        )
        rows = cursor.fetchall()
        if not rows:
            return

        now = datetime.utcnow().isoformat(timespec="seconds")
        for row in rows:
            symbol, order_id, side, quantity, entry_price, entry_time, strategy_name, stop_loss, updated_at = row
            position_id = f"migrated-{symbol}-{updated_at or now}"
            cursor.execute(
                """
                INSERT OR IGNORE INTO strategy_positions (
                    strategy_name, symbol, position_id, instrument_id, venue,
                    trade_id, side, quantity, exchange_entry_price,
                    strategy_entry_price, stop_price,
                    status, entry_time, recovery_reason,
                    original_run_id, last_run_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'BYBIT', ?, ?, ?, ?, ?, ?, 'recovered', ?, 'schema_migration', 'migration', 'migration', ?, ?)
                """,
                (
                    strategy_name or "unknown",
                    symbol,
                    position_id,
                    symbol,
                    order_id or position_id,
                    side or "LONG",
                    float(quantity or 0),
                    float(entry_price or 0),
                    float(entry_price or 0),
                    float(stop_loss) if stop_loss is not None else None,
                    entry_time,
                    now,
                    now,
                ),
            )

    def record_discovery(self, data: Dict):
        """Insert or refresh a discovery event while avoiding duplicate pending rows."""
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM discovery_journal
                WHERE symbol = ? AND status = 'pending'
                ORDER BY discovery_time DESC
                LIMIT 1
                """,
                (data["symbol"],),
            )
            existing = cursor.fetchone()

            if existing:
                updates = {
                    "last_seen_time": now,
                    "entry_volume_24h": max(
                        float(existing["entry_volume_24h"] or 0.0),
                        float(data["volume_24h"]),
                    ),
                    "discovery_score": max(
                        float(existing["discovery_score"] or 0.0), float(data["score"])
                    ),
                    "regime": data.get("regime") or existing["regime"] or "UNKNOWN",
                    "discovery_source": data.get("discovery_source")
                    or existing["discovery_source"]
                    or "scanner_v2",
                    "scanner_profile": data.get("scanner_profile")
                    or existing["scanner_profile"],
                    "direction": data.get("direction") or existing["direction"],
                    "liquidity_score": max(
                        float(existing["liquidity_score"] or 0.0),
                        float(data.get("liquidity_score", 0.0) or 0.0),
                    ),
                    "burst_score": max(
                        float(existing["burst_score"] or 0.0),
                        float(data.get("burst_score", 0.0) or 0.0),
                    ),
                    "breakout_score": max(
                        float(existing["breakout_score"] or 0.0),
                        float(data.get("breakout_score", 0.0) or 0.0),
                    ),
                    "discovery_change_pct": (
                        data.get("change")
                        if data.get("change") is not None
                        else existing["discovery_change_pct"]
                    ),
                    "discovery_count": int(existing["discovery_count"] or 1) + 1,
                    "max_price": max(
                        float(existing["max_price"] or data["last_price"]),
                        float(data["last_price"]),
                    ),
                    "min_price": min(
                        float(existing["min_price"] or data["last_price"]),
                        float(data["last_price"]),
                    ),
                    "scanner_metadata_json": json.dumps(
                        data.get("metadata", {}),
                        sort_keys=True,
                    ),
                }
                self._update_row(conn, "discovery_journal", existing["id"], updates)
                return int(existing["id"]), False

            query = """
                INSERT INTO discovery_journal (
                    symbol,
                    discovery_time,
                    last_seen_time,
                    entry_price,
                    entry_volume_24h,
                    discovery_score,
                    discovery_source,
                    scanner_profile,
                    direction,
                    liquidity_score,
                    burst_score,
                    breakout_score,
                    regime,
                    discovery_change_pct,
                    discovery_count,
                    scanner_metadata_json,
                    max_price,
                    min_price,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                data["symbol"],
                now,
                now,
                data["last_price"],
                data["volume_24h"],
                data["score"],
                data.get("discovery_source", "scanner_v2"),
                data.get("scanner_profile"),
                data.get("direction"),
                data.get("liquidity_score"),
                data.get("burst_score"),
                data.get("breakout_score"),
                data.get("regime", "UNKNOWN"),
                data.get("change"),
                1,
                json.dumps(data.get("metadata", {}), sort_keys=True),
                data["last_price"],
                data["last_price"],
                "pending",
            )
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid, True

    def get_pending_discoveries(self) -> List[Dict]:
        """Fetch all discoveries that are still being tracked."""
        query = "SELECT * FROM discovery_journal WHERE status = 'pending'"
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def update_discovery(self, discovery_id: int, updates: Dict):
        """Update performance metrics for a discovery."""
        with self._get_connection() as conn:
            self._update_row(conn, "discovery_journal", discovery_id, updates)

    def get_journal_summary(self, days: int = 7) -> List[Dict]:
        """Fetch summary of recent discoveries."""
        query = """
            SELECT * FROM discovery_journal 
            WHERE discovery_time >= datetime('now', ?)
            ORDER BY discovery_time DESC
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, (f"-{days} days",))
            return [dict(row) for row in cursor.fetchall()]

    def get_completed_discoveries(self, days: int = 14) -> List[Dict]:
        """Fetch completed discoveries for analysis and tuning."""
        query = """
            SELECT * FROM discovery_journal
            WHERE status = 'completed'
              AND discovery_time >= datetime('now', ?)
            ORDER BY discovery_time DESC
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, (f"-{days} days",))
            return [dict(row) for row in cursor.fetchall()]

    def record_trade(self, trade: Trade):
        """Persist a completed trade."""
        query = """
            INSERT INTO trades (
                symbol,
                side,
                quantity,
                entry_price,
                exit_price,
                entry_time,
                exit_time,
                pnl,
                pnl_pct,
                strategy_name,
                regime,
                reason_entry,
                reason_exit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            trade.symbol,
            trade.side,
            trade.quantity,
            trade.entry_price,
            trade.exit_price,
            trade.entry_time.isoformat(timespec="seconds"),
            trade.exit_time.isoformat(timespec="seconds"),
            trade.pnl,
            trade.pnl_pct,
            trade.strategy_name,
            trade.regime.value if hasattr(trade.regime, "value") else str(trade.regime),
            trade.reason_entry,
            trade.reason_exit,
        )
        with self._get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def upsert_managed_position(self, managed: Dict[str, Any]):
        """Persist or refresh runtime-managed position metadata keyed by symbol."""
        signal = managed.get("signal")
        signal_payload = None
        strategy_name = managed.get("strategy_name", "")
        stop_loss = managed.get("stop_loss")
        take_profit = managed.get("take_profit")

        if signal is not None:
            signal_payload = json.dumps(signal.model_dump(mode="json"), sort_keys=True)
            strategy_name = getattr(signal, "strategy_name", strategy_name)
            stop_loss = getattr(signal, "stop_loss", stop_loss)
            take_profit = getattr(signal, "take_profit", take_profit)

        side = managed.get("side")
        if hasattr(side, "value"):
            side = side.value

        entry_time = managed.get("entry_time")
        if isinstance(entry_time, datetime):
            entry_time = entry_time.isoformat(timespec="seconds")

        query = """
            INSERT INTO managed_positions (
                symbol,
                order_id,
                side,
                quantity,
                entry_price,
                entry_time,
                strategy_name,
                stop_loss,
                take_profit,
                signal_payload_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(symbol) DO UPDATE SET
                order_id = excluded.order_id,
                side = excluded.side,
                quantity = excluded.quantity,
                entry_price = excluded.entry_price,
                entry_time = excluded.entry_time,
                strategy_name = excluded.strategy_name,
                stop_loss = excluded.stop_loss,
                take_profit = excluded.take_profit,
                signal_payload_json = excluded.signal_payload_json,
                updated_at = CURRENT_TIMESTAMP
        """
        params = (
            managed["symbol"],
            managed.get("order_id"),
            side,
            float(managed.get("quantity") or 0.0),
            float(managed.get("entry_price") or 0.0),
            entry_time,
            strategy_name,
            stop_loss,
            take_profit,
            signal_payload,
        )
        with self._get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def get_managed_positions(self) -> List[Dict]:
        """Fetch persisted managed-position metadata for runtime recovery."""
        query = "SELECT * FROM managed_positions ORDER BY symbol"
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def delete_managed_position(self, symbol: str):
        """Delete persisted runtime metadata for a managed symbol."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM managed_positions WHERE symbol = ?", (symbol,))
            conn.commit()

    # --- Strategy Position Recovery Methods ---

    def upsert_strategy_position(self, data: Dict[str, Any]) -> None:
        """Insert or update a strategy position row."""
        now = datetime.utcnow().isoformat(timespec="seconds")
        query = """
            INSERT INTO strategy_positions (
                strategy_name, symbol, position_id, instrument_id, venue,
                trade_id, side, quantity, exchange_entry_price,
                strategy_entry_price, entry_atr, stop_price, best_price,
                trailing_active, bars_in_position,
                entry_time, last_bar_time, status,
                entry_reason, strategy_state_json,
                original_run_id, last_run_id,
                created_at, updated_at
            ) VALUES (
                :strategy_name, :symbol, :position_id, :instrument_id, :venue,
                :trade_id, :side, :quantity, :exchange_entry_price,
                :strategy_entry_price, :entry_atr, :stop_price, :best_price,
                :trailing_active, :bars_in_position,
                :entry_time, :last_bar_time, :status,
                :entry_reason, :strategy_state_json,
                :original_run_id, :last_run_id,
                :created_at, :updated_at
            )
            ON CONFLICT(strategy_name, symbol, position_id) DO UPDATE SET
                quantity = excluded.quantity,
                exchange_entry_price = excluded.exchange_entry_price,
                stop_price = excluded.stop_price,
                best_price = excluded.best_price,
                trailing_active = excluded.trailing_active,
                bars_in_position = excluded.bars_in_position,
                last_bar_time = excluded.last_bar_time,
                status = excluded.status,
                strategy_state_json = excluded.strategy_state_json,
                last_run_id = excluded.last_run_id,
                updated_at = excluded.updated_at
        """
        params = {
            "strategy_name": data["strategy_name"],
            "symbol": data["symbol"],
            "position_id": data["position_id"],
            "instrument_id": data.get("instrument_id", data["symbol"]),
            "venue": data.get("venue", "BYBIT"),
            "trade_id": data["trade_id"],
            "side": data["side"],
            "quantity": float(data.get("quantity", 0)),
            "exchange_entry_price": float(data.get("exchange_entry_price", 0)),
            "strategy_entry_price": (
                float(data["strategy_entry_price"])
                if data.get("strategy_entry_price") is not None
                else None
            ),
            "entry_atr": (
                float(data["entry_atr"]) if data.get("entry_atr") is not None else None
            ),
            "stop_price": (
                float(data["stop_price"]) if data.get("stop_price") is not None else None
            ),
            "best_price": (
                float(data["best_price"]) if data.get("best_price") is not None else None
            ),
            "trailing_active": int(bool(data.get("trailing_active", False))),
            "bars_in_position": int(data.get("bars_in_position", 0)),
            "entry_time": data.get("entry_time"),
            "last_bar_time": data.get("last_bar_time", now),
            "status": data.get("status", "open"),
            "entry_reason": data.get("entry_reason"),
            "strategy_state_json": (
                json.dumps(data["strategy_state_json"], sort_keys=True)
                if data.get("strategy_state_json") is not None
                else None
            ),
            "original_run_id": data.get("original_run_id"),
            "last_run_id": data.get("last_run_id"),
            "created_at": data.get("created_at", now),
            "updated_at": now,
        }
        with self._get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def get_open_strategy_positions(self) -> List[Dict]:
        """Fetch all strategy positions with an active status."""
        query = """
            SELECT * FROM strategy_positions
            WHERE status IN ('opening', 'open', 'recovered', 'adopted')
            ORDER BY strategy_name, symbol
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def mark_position_closed(
        self,
        strategy_name: str,
        symbol: str,
        position_id: str,
        exit_reason: str,
    ) -> None:
        """Mark a strategy position as closed."""
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE strategy_positions
                SET status = 'closed', exit_reason = ?, closed_at = ?, updated_at = ?
                WHERE strategy_name = ? AND symbol = ? AND position_id = ?
                """,
                (exit_reason, now, now, strategy_name, symbol, position_id),
            )
            conn.commit()

    def mark_position_externally_closed(
        self,
        strategy_name: str,
        symbol: str,
        position_id: str,
    ) -> None:
        """Mark a strategy position as closed by an external actor."""
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE strategy_positions
                SET status = 'externally_closed', closed_at = ?, updated_at = ?
                WHERE strategy_name = ? AND symbol = ? AND position_id = ?
                """,
                (now, now, strategy_name, symbol, position_id),
            )
            conn.commit()

    def upsert_strategy_order(self, data: Dict[str, Any]) -> None:
        """Insert or update a strategy order row."""
        now = datetime.utcnow().isoformat(timespec="seconds")
        query = """
            INSERT INTO strategy_orders (
                client_order_id, venue_order_id, position_id,
                trade_id, strategy_name, symbol, instrument_id,
                side, order_type, quantity, status, reduce_only,
                submitted_at, filled_at, closed_at,
                created_at, updated_at, raw_event_json
            ) VALUES (
                :client_order_id, :venue_order_id, :position_id,
                :trade_id, :strategy_name, :symbol, :instrument_id,
                :side, :order_type, :quantity, :status, :reduce_only,
                :submitted_at, :filled_at, :closed_at,
                :created_at, :updated_at, :raw_event_json
            )
            ON CONFLICT(client_order_id) DO UPDATE SET
                venue_order_id = COALESCE(excluded.venue_order_id, strategy_orders.venue_order_id),
                position_id = COALESCE(excluded.position_id, strategy_orders.position_id),
                status = excluded.status,
                filled_at = COALESCE(excluded.filled_at, strategy_orders.filled_at),
                closed_at = COALESCE(excluded.closed_at, strategy_orders.closed_at),
                updated_at = excluded.updated_at,
                raw_event_json = COALESCE(excluded.raw_event_json, strategy_orders.raw_event_json)
        """
        params = {
            "client_order_id": data["client_order_id"],
            "venue_order_id": data.get("venue_order_id"),
            "position_id": data.get("position_id"),
            "trade_id": data["trade_id"],
            "strategy_name": data["strategy_name"],
            "symbol": data["symbol"],
            "instrument_id": data.get("instrument_id", data["symbol"]),
            "side": data["side"],
            "order_type": data.get("order_type"),
            "quantity": float(data.get("quantity", 0)),
            "status": data["status"],
            "reduce_only": int(bool(data.get("reduce_only", False))),
            "submitted_at": data.get("submitted_at"),
            "filled_at": data.get("filled_at"),
            "closed_at": data.get("closed_at"),
            "created_at": data.get("created_at", now),
            "updated_at": now,
            "raw_event_json": (
                json.dumps(data["raw_event_json"], sort_keys=True)
                if data.get("raw_event_json") is not None
                else None
            ),
        }
        with self._get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def create_reconciliation_run(
        self,
        run_id: str,
        trigger: str,
    ) -> None:
        """Start a reconciliation audit row."""
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO reconciliation_runs (run_id, started_at, trigger, result)
                VALUES (?, ?, ?, 'running')
                """,
                (run_id, now, trigger),
            )
            conn.commit()

    def finish_reconciliation_run(
        self,
        run_id: str,
        result: str,
        counts: Dict[str, int],
    ) -> None:
        """Complete a reconciliation audit row."""
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE reconciliation_runs
                SET finished_at = ?,
                    result = ?,
                    exchange_open_count = ?,
                    db_open_count = ?,
                    recovered_count = ?,
                    adopted_count = ?,
                    externally_closed_count = ?,
                    blocked_count = ?,
                    failed_count = ?,
                    details_json = ?
                WHERE run_id = ?
                """,
                (
                    now,
                    result,
                    counts.get("exchange_open", 0),
                    counts.get("db_open", 0),
                    counts.get("recovered", 0),
                    counts.get("adopted", 0),
                    counts.get("externally_closed", 0),
                    counts.get("blocked", 0),
                    counts.get("failed", 0),
                    json.dumps(counts, sort_keys=True),
                    run_id,
                ),
            )
            conn.commit()

    def get_recent_trades(self, days: int = 14) -> List[Dict]:
        """Fetch recent persisted trades for analytics."""
        query = """
            SELECT * FROM trades
            WHERE exit_time >= datetime('now', ?)
            ORDER BY exit_time DESC
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, (f"-{days} days",))
            return [dict(row) for row in cursor.fetchall()]

    def record_parameter_suggestions(self, suggestions: List[Dict[str, Any]]):
        """Persist passive parameter suggestions generated from recent outcomes."""
        if not suggestions:
            return

        query = """
            INSERT INTO parameter_suggestions (
                source,
                parameter_name,
                current_value,
                suggested_value,
                confidence,
                rationale,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        with self._get_connection() as conn:
            conn.executemany(
                query,
                [
                    (
                        suggestion["source"],
                        suggestion["parameter_name"],
                        suggestion.get("current_value"),
                        suggestion.get("suggested_value"),
                        suggestion.get("confidence"),
                        suggestion.get("rationale"),
                        json.dumps(suggestion.get("metadata", {})),
                    )
                    for suggestion in suggestions
                ],
            )
            conn.commit()

    @staticmethod
    def _update_row(
        conn: sqlite3.Connection, table: str, row_id: int, updates: Dict[str, Any]
    ):
        if not updates:
            return
        fields = ", ".join([f"{k} = ?" for k in updates.keys()])
        query = f"UPDATE {table} SET {fields} WHERE id = ?"
        params = list(updates.values()) + [row_id]
        conn.execute(query, params)
        conn.commit()
