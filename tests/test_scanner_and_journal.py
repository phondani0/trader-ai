from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta

from src.core.database import DatabaseManager
from src.core.models import MarketRegime, Side, Trade
from src.intelligence.journal import DiscoveryJournal
from src.intelligence.scanner import MarketScanner
from tests.helpers import StubBroker


class ScannerAndJournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(db_path=f"{self.tmp_dir.name}/test.db")
        self.config = {
            "discovery": {
                "min_24h_volume": 10_000_000,
                "min_price_change": 3.0,
                "min_trade_score": 3.0,
                "top_n": 5,
                "candidate_ttl_minutes": 30,
            },
            "watchlist": {"max_stocks": 20},
            "adaptation": {
                "enabled": True,
                "suggestion_lookback_days": 14,
                "min_completed_discoveries": 10,
                "min_recent_trades": 5,
            },
            "vwap_reversion": {"stop_loss_pct": 1.5},
        }
        self.broker = StubBroker(
            tickers={
                "BTC/USDT:USDT": {
                    "last": 100.0,
                    "bid": 99.5,
                    "ask": 100.5,
                    "change": 5.0,
                    "quoteVolume": 20_000_000,
                },
                "ETH/USDT:USDT": {
                    "last": 50.0,
                    "bid": 49.5,
                    "ask": 50.5,
                    "change": 1.0,
                    "quoteVolume": 5_000_000,
                },
            }
        )
        self.journal = DiscoveryJournal(self.broker, self.db, config=self.config)
        self.scanner = MarketScanner(self.broker, self.config, self.journal)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_scanner_filters_and_deduplicates_pending_journal_entries(self):
        first_scan = self.scanner.scan_for_momentum()
        second_scan = self.scanner.scan_for_momentum()
        pending = self.db.get_pending_discoveries()

        self.assertEqual(len(first_scan), 1)
        self.assertEqual(len(second_scan), 1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(int(pending[0]["discovery_count"]), 2)

        tradable_universe = self.scanner.build_tradable_universe(
            ["ETH/USDT:USDT"], persistent_symbols=["LTC/USDT:USDT"]
        )
        self.assertEqual(
            tradable_universe[:3],
            ["ETH/USDT:USDT", "LTC/USDT:USDT", "BTC/USDT:USDT"],
        )

    def test_journal_generates_passive_parameter_suggestions(self):
        for idx in range(10):
            discovery_id, _ = self.db.record_discovery(
                {
                    "symbol": f"ALT{idx}/USDT:USDT",
                    "last_price": 100.0,
                    "volume_24h": 20_000_000,
                    "score": 5.0,
                    "change": 6.0,
                }
            )
            self.db.update_discovery(
                discovery_id,
                {
                    "price_1h": 99.0,
                    "price_4h": 98.0,
                    "price_24h": 97.0,
                    "max_price": 101.0,
                    "min_price": 96.0,
                    "status": "completed",
                },
            )

        for idx in range(5):
            trade = Trade(
                symbol=f"ALT{idx}/USDT:USDT",
                side=Side.BUY,
                quantity=1.0,
                entry_price=100.0,
                exit_price=97.0,
                entry_time=datetime.utcnow() - timedelta(hours=2),
                exit_time=datetime.utcnow() - timedelta(hours=1),
                strategy_name="vwap_reversion",
                regime=MarketRegime.SIDEWAYS_LOW_VOL,
                reason_entry="test",
                reason_exit="stop",
            )
            trade.calculate_pnl()
            self.db.record_trade(trade)

        suggestions = self.journal.generate_parameter_suggestions()
        parameter_names = {suggestion["parameter_name"] for suggestion in suggestions}

        self.assertIn("discovery.min_price_change", parameter_names)
        self.assertIn("vwap_reversion.stop_loss_pct", parameter_names)


if __name__ == "__main__":
    unittest.main()
