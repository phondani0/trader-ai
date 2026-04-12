"""
Discovery Journal — Feedback Loop Logic.
Tracks the performance of coins found by the scanner over time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from loguru import logger

from src.core.database import DatabaseManager
from src.core.base_broker import BaseBroker


class DiscoveryJournal:
    """
    Manages the feedback loop for market discovery.
    Records snapshots and evaluates accuracy at 1h, 4h, and 24h intervals.
    """

    def __init__(self, broker: BaseBroker, db: DatabaseManager):
        self.broker = broker
        self.db = db

    def record(self, discovery_data: Dict):
        """Record a new momentum discovery in the journal."""
        try:
            discovery_id = self.db.record_discovery(discovery_data)
            logger.info(f"📓 Journal entry created for {discovery_data['symbol']} (ID: {discovery_id})")
        except Exception as e:
            logger.error(f"Failed to journal discovery: {e}")

    def update_performance(self):
        """
        Check pending discoveries and update their performance metrics
        if the time window has passed.
        """
        pending = self.db.get_pending_discoveries()
        if not pending:
            return

        logger.info(f"🧐 Reviewing {len(pending)} pending discovery journals...")
        now = datetime.utcnow()

        for entry in pending:
            symbol = entry["symbol"]
            discovery_time = datetime.strptime(entry["discovery_time"], "%Y-%m-%d %H:%M:%S")
            entry_price = entry["entry_price"]
            
            # Fetch current price
            current_ticker = self.broker.fetch_ticker(symbol)
            current_price = current_ticker.get("last", 0.0)
            if not current_price:
                continue

            updates: Dict = {}
            elapsed = now - discovery_time

            # Check windows
            if not entry["price_1h"] and elapsed >= timedelta(hours=1):
                updates["price_1h"] = current_price
            
            if not entry["price_4h"] and elapsed >= timedelta(hours=4):
                updates["price_4h"] = current_price
            
            if not entry["price_24h"] and elapsed >= timedelta(hours=24):
                updates["price_24h"] = current_price
                updates["status"] = "completed"

            # Track Max High observed
            prev_max = entry.get("max_price", 0.0) or 0.0
            if current_price > prev_max:
                updates["max_price"] = current_price

            if updates:
                self.db.update_discovery(entry["id"], updates)
                logger.debug(f"📝 Updated journal for {symbol}: {updates}")

    def generate_short_report(self) -> str:
        """Generate a summary of recent discovery performance."""
        recent = self.db.get_journal_summary(days=1)
        if not recent:
            return "No recent discoveries to report."

        total = len(recent)
        completed = [r for r in recent if r["status"] == "completed"]
        
        report = [
            "\n" + "═" * 60,
            "📊 DISCOVERY PERFORMANCE REPORT (24H)",
            "═" * 60,
            f"{'SYMBOL':<15} | {'ENTRY':<10} | {'MAX %':<10} | {'STATUS'}",
            "-" * 60
        ]

        for r in recent:
            max_p = r.get("max_price", r["entry_price"])
            max_move = ((max_p / r["entry_price"]) - 1) * 100
            
            status_emoji = "✅" if r["status"] == "completed" else "⏳"
            report.append(
                f"{r['symbol']:<15} | {r['entry_price']:<10.4f} | {max_move:<10.2f}% | {status_emoji} {r['status']}"
            )

        report.append("═" * 60 + "\n")
        return "\n".join(report)
