"""
Database Manager.
Handles persistence for trades, discoveries, and performance metrics.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


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
                    entry_price REAL,
                    entry_volume_24h REAL,
                    discovery_score REAL,
                    regime TEXT,
                    price_1h REAL,
                    price_4h REAL,
                    price_24h REAL,
                    max_price REAL,
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
                    reason_exit TEXT
                )
                """
            )
            conn.commit()
        logger.info(f"💾 Database initialized at {self.db_path}")

    def record_discovery(self, data: Dict):
        """Insert a new discovery event."""
        query = """
            INSERT INTO discovery_journal (
                symbol, entry_price, entry_volume_24h, discovery_score, regime, status
            ) VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (
            data["symbol"],
            data["last_price"],
            data["volume_24h"],
            data["score"],
            data.get("regime", "UNKNOWN"),
            "pending",
        )
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid

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
        fields = ", ".join([f"{k} = ?" for k in updates.keys()])
        query = f"UPDATE discovery_journal SET {fields} WHERE id = ?"
        params = list(updates.values()) + [discovery_id]

        with self._get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

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
