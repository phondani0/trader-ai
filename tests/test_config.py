from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.config import load_settings
from trader_ai.config import load_strategy_configs


class ConfigLoadingTest(unittest.TestCase):
    def test_load_settings_reads_expected_defaults(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")

        self.assertEqual(settings.venue, "BYBIT")
        self.assertEqual(settings.bar_type, "XRPUSDT-LINEAR.BYBIT-15-MINUTE-LAST-EXTERNAL")
        self.assertEqual(settings.trade_size, "1.0")
        self.assertEqual(settings.fast_ema_period, 10)
        self.assertEqual(settings.slow_ema_period, 20)
        self.assertEqual(settings.bars_count, 10000)
        self.assertTrue(settings.warmup_enabled)
        self.assertEqual(settings.warmup_bars, 120)

    def test_load_strategy_configs_reads_execution_matrix(self):
        strategies = load_strategy_configs(PROJECT_ROOT / "config" / "strategies.yaml")

        adaptive = strategies["adaptive_trend_rider"]
        self.assertTrue(adaptive.enabled)
        self.assertEqual(adaptive.discovery_gate, "soft")
        self.assertEqual(
            adaptive.instruments,
            ["btcusdt_linear_bybit", "ethusdt_linear_bybit", "xrpusdt_linear_bybit"],
        )
        self.assertEqual(adaptive.params["fast_ema_period"], 10)
        self.assertEqual(adaptive.params["slow_ema_period"], 20)
        self.assertTrue(adaptive.scanner_execution["enabled"])
        self.assertEqual(adaptive.scanner_execution["max_symbols"], 5)
        self.assertEqual(adaptive.scanner_execution["min_score"], 10.0)
        self.assertEqual(adaptive.scanner_execution["min_24h_volume"], 50_000_000.0)
        self.assertEqual(adaptive.scanner_execution["discovery_gate"], "hard")

        volume_spike = strategies["volume_spike_trend"]
        self.assertTrue(volume_spike.enabled)
        self.assertEqual(volume_spike.discovery_gate, "soft")
        self.assertEqual(volume_spike.instruments, ["ethusdt_linear_bybit"])
        self.assertTrue(volume_spike.scanner_execution["enabled"])
        self.assertEqual(volume_spike.scanner_execution["max_symbols"], 3)
        self.assertEqual(volume_spike.scanner_execution["min_score"], 12.0)
        self.assertEqual(volume_spike.scanner_execution["min_24h_volume"], 50_000_000.0)
        self.assertEqual(volume_spike.scanner_execution["discovery_gate"], "hard")
        self.assertEqual(volume_spike.params["volume_multiplier"], 3.0)

        squeeze = strategies["squeeze_breakout"]
        self.assertTrue(squeeze.enabled)
        self.assertEqual(squeeze.discovery_gate, "soft")
        self.assertEqual(squeeze.instruments, ["btcusdt_linear_bybit"])
        self.assertTrue(squeeze.scanner_execution["enabled"])
        self.assertEqual(squeeze.scanner_execution["max_symbols"], 2)
        self.assertEqual(squeeze.scanner_execution["min_score"], 14.0)
        self.assertEqual(squeeze.scanner_execution["min_24h_volume"], 75_000_000.0)
        self.assertEqual(squeeze.scanner_execution["discovery_gate"], "hard")
        self.assertEqual(squeeze.params["trade_notional_usdt"], "25")


if __name__ == "__main__":
    unittest.main()
