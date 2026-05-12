from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta

from src.core.database import DatabaseManager
from src.core.models import MarketRegime, Side, Trade
from src.intelligence.journal import DiscoveryJournal
from src.intelligence.scanner import MarketScanner
from tests.helpers import StubBroker, build_price_frame


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
                "dynamic_monitor_size": 3,
                "liquid_universe_size": 5,
                "feature_timeframe": "5m",
                "feature_window_bars": 48,
                "breakout_lookback_bars": 12,
                "momentum_lookback_bars": 12,
                "acceleration_lookback_bars": 3,
                "relative_volume_lookback_bars": 12,
                "min_relative_volume": 1.1,
                "min_intraday_momentum_pct": 1.0,
                "min_breakout_score": 1.0,
                "max_spread_bps": 25.0,
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
                    "bid": 99.98,
                    "ask": 100.02,
                    "change": 1.5,
                    "quoteVolume": 30_000_000,
                },
                "ETH/USDT:USDT": {
                    "last": 50.0,
                    "bid": 49.99,
                    "ask": 50.01,
                    "change": 1.0,
                    "quoteVolume": 18_000_000,
                },
                "SOL/USDT:USDT": {
                    "last": 55.0,
                    "bid": 54.99,
                    "ask": 55.01,
                    "change": 1.8,
                    "quoteVolume": 25_000_000,
                },
            },
            ohlcv={
                "BTC/USDT:USDT": self._build_flat_frame(100.0),
                "ETH/USDT:USDT": self._build_flat_frame(50.0),
                "SOL/USDT:USDT": self._build_breakout_frame(48.0),
            },
        )
        self.journal = DiscoveryJournal(self.broker, self.db, config=self.config)
        self.scanner = MarketScanner(self.broker, self.config, self.journal)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _build_flat_frame(self, start_price: float):
        rows = []
        base_time = datetime(2026, 1, 1)
        price = start_price
        for idx in range(40):
            price *= 1.0002
            rows.append(
                {
                    "timestamp": base_time + timedelta(minutes=5 * idx),
                    "open": price * 0.999,
                    "high": price * 1.001,
                    "low": price * 0.999,
                    "close": price,
                    "volume": 1_000 + (idx % 3) * 20,
                }
            )
        return build_price_frame(rows)

    def _build_breakout_frame(self, start_price: float):
        rows = []
        base_time = datetime(2026, 1, 1)
        price = start_price
        for idx in range(40):
            if idx < 24:
                price *= 1.001
                volume = 900
            elif idx < 36:
                price *= 1.004
                volume = 1_300
            else:
                price *= 1.01
                volume = 3_500 if idx < 39 else 6_500
            rows.append(
                {
                    "timestamp": base_time + timedelta(minutes=5 * idx),
                    "open": price * 0.997,
                    "high": price * 1.003,
                    "low": price * 0.996,
                    "close": price,
                    "volume": volume,
                }
            )
        return build_price_frame(rows)

    def test_scanner_filters_and_deduplicates_pending_journal_entries(self):
        first_scan = self.scanner.scan_for_momentum()
        second_scan = self.scanner.scan_for_momentum()
        pending = self.db.get_pending_discoveries()

        self.assertEqual(len(first_scan), 1)
        self.assertEqual(len(second_scan), 1)
        self.assertEqual(first_scan[0]["symbol"], "SOL/USDT:USDT")
        self.assertEqual(first_scan[0]["discovery_source"], "scanner_v2")
        self.assertEqual(first_scan[0]["scanner_profile"], "breakout")
        self.assertGreater(first_scan[0]["relative_volume"], 1.1)
        self.assertGreater(first_scan[0]["breakout_score"], 1.0)
        self.assertEqual(len(pending), 1)
        self.assertEqual(int(pending[0]["discovery_count"]), 2)
        self.assertEqual(pending[0]["discovery_source"], "scanner_v2")
        self.assertEqual(pending[0]["scanner_profile"], "breakout")
        self.assertEqual(pending[0]["direction"], "long")
        self.assertGreater(float(pending[0]["liquidity_score"]), 0.0)
        self.assertGreater(float(pending[0]["burst_score"]), 0.0)
        self.assertGreater(float(pending[0]["breakout_score"]), 0.0)
        self.assertIn("relative_volume", json.loads(pending[0]["scanner_metadata_json"]))

        tradable_universe = self.scanner.build_tradable_universe(
            ["ETH/USDT:USDT"], persistent_symbols=["LTC/USDT:USDT"]
        )
        self.assertEqual(tradable_universe[:2], ["ETH/USDT:USDT", "LTC/USDT:USDT"])
        self.assertIn("SOL/USDT:USDT", tradable_universe)
        self.assertIn("BTC/USDT:USDT", self.scanner.get_dynamic_universe_symbols())

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
