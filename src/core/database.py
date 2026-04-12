"""
Database Manager.
Handles persistence for trades, discoveries, and performance metrics.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from src.core.models import Trade


class DatabaseManager:
    """
    SQLite database manager for persistent trading records.
    """

    def __init__(self, db_path: str = "data/trader.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

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
                    regime TEXT,
                    discovery_change_pct REAL,
                    discovery_count INTEGER DEFAULT 1,
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

            self._ensure_column(
                cursor, "discovery_journal", "last_seen_time", "TIMESTAMP"
            )
            self._ensure_column(
                cursor, "discovery_journal", "discovery_change_pct", "REAL"
            )
            self._ensure_column(
                cursor,
                "discovery_journal",
                "discovery_count",
                "INTEGER DEFAULT 1",
            )
            self._ensure_column(cursor, "discovery_journal", "min_price", "REAL")
            self._ensure_column(cursor, "trades", "regime", "TEXT")
            self._ensure_column(cursor, "trades", "reason_entry", "TEXT")
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
                    regime,
                    discovery_change_pct,
                    discovery_count,
                    max_price,
                    min_price,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                data["symbol"],
                now,
                now,
                data["last_price"],
                data["volume_24h"],
                data["score"],
                data.get("regime", "UNKNOWN"),
                data.get("change"),
                1,
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
            trade.side.value,
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
