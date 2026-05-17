from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.strategies.registry import create_strategy
from trader_ai.strategies.registry import list_strategies


class StrategyRegistryTest(unittest.TestCase):
    def test_registry_lists_expected_strategy_names(self):
        names = list_strategies()
        self.assertEqual(
            names,
            ["adaptive_trend_rider", "demo_ema_cross", "nautilus_ema_cross", "squeeze_breakout", "volume_spike_trend"],
        )

    def test_registry_builds_official_nautilus_ema_cross_strategy(self):
        strategy = create_strategy(
            name="nautilus_ema_cross",
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL",
            trade_size="0.010",
            fast_ema_period=8,
            slow_ema_period=21,
        )

        self.assertEqual(strategy.__class__.__name__, "EMACross")

    def test_registry_builds_custom_volume_spike_strategy(self):
        strategy = create_strategy(
            name="volume_spike_trend",
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL",
            trade_size="0.010",
            fast_ema_period=8,
            slow_ema_period=21,
        )

        self.assertEqual(strategy.__class__.__name__, "VolumeSpikeTrendStrategy")

    def test_registry_applies_strategy_params_and_discovery_gate(self):
        strategy = create_strategy(
            name="adaptive_trend_rider",
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL",
            trade_size="0.010",
            fast_ema_period=8,
            slow_ema_period=21,
            discovery_gate="soft",
            params={
                "fast_ema_period": 13,
                "slow_ema_period": 34,
                "volume_spike_mult": 2.2,
            },
        )

        self.assertEqual(strategy.config.fast_ema_period, 13)
        self.assertEqual(strategy.config.slow_ema_period, 34)
        self.assertEqual(strategy.config.volume_spike_mult, 2.2)
        self.assertEqual(strategy.config.discovery_gate, "soft")
        self.assertFalse(strategy.config.use_discovery_journal)

    def test_soft_discovery_gate_does_not_block_without_hot_journal_symbol(self):
        strategy = create_strategy(
            name="adaptive_trend_rider",
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL",
            trade_size="0.010",
            fast_ema_period=8,
            slow_ema_period=21,
            discovery_gate="soft",
            ccxt_symbol="BTC/USDT:USDT",
        )
        strategy.db = Mock()
        strategy.db.get_pending_discoveries.return_value = [
            {"symbol": "ETH/USDT:USDT"}
        ]

        self.assertFalse(strategy._journal_blocks_entry())

    def test_hard_discovery_gate_blocks_without_hot_journal_symbol(self):
        strategy = create_strategy(
            name="adaptive_trend_rider",
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL",
            trade_size="0.010",
            fast_ema_period=8,
            slow_ema_period=21,
            discovery_gate="hard",
            ccxt_symbol="BTC/USDT:USDT",
        )
        strategy.db = Mock()
        strategy.db.get_pending_discoveries.return_value = [
            {"symbol": "ETH/USDT:USDT"}
        ]

        self.assertTrue(strategy._journal_blocks_entry())

    def test_registry_rejects_unknown_strategy_names(self):
        with self.assertRaises(ValueError):
            create_strategy(
                name="supertrend",
                instrument_id="BTCUSDT-PERP.BINANCE",
                bar_type="BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL",
                trade_size="0.010",
                fast_ema_period=8,
                slow_ema_period=21,
            )


if __name__ == "__main__":
    unittest.main()
