from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from nautilus_app.strategies.registry import create_strategy
from nautilus_app.strategies.registry import list_strategies


class StrategyRegistryTest(unittest.TestCase):
    def test_registry_lists_expected_strategy_names(self):
        names = list_strategies()
        self.assertEqual(
            names,
            ["demo_ema_cross", "nautilus_ema_cross", "volume_spike_trend"],
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
