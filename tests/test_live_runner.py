from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.runners import live  # noqa: E402


class LiveRunnerTimerCallbackTest(unittest.TestCase):
    def test_discovery_timer_callback_accepts_nautilus_time_event(self):
        scanner = Mock()

        with patch.object(live, "_scanner", scanner):
            live._trigger_discovery_scan(object())

        scanner.scan_for_momentum.assert_called_once_with()

    def test_journal_timer_callback_accepts_nautilus_time_event(self):
        journal = Mock()

        with patch.object(live, "_journal", journal):
            live._trigger_journal_review(object())

        journal.update_performance.assert_called_once_with()

    def test_build_live_components_uses_enabled_strategy_matrix(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")

        components = live.build_live_components(settings)

        pairs = [
            (component.strategy_name, component.instrument_spec.ccxt_symbol)
            for component in components
        ]
        self.assertEqual(
            pairs,
            [
                ("adaptive_trend_rider", "BTC/USDT:USDT"),
                ("adaptive_trend_rider", "ETH/USDT:USDT"),
                ("adaptive_trend_rider", "XRP/USDT:USDT"),
            ],
        )
        self.assertTrue(
            all(
                component.strategy.config.discovery_gate == "soft"
                for component in components
            )
        )

    def test_build_live_components_preserves_cli_single_strategy_override(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")

        components = live.build_live_components(
            settings,
            instrument_name="xrpusdt_linear_bybit",
            strategy_name="volume_spike_trend",
        )

        self.assertEqual(len(components), 1)
        self.assertEqual(components[0].strategy_name, "volume_spike_trend")
        self.assertEqual(components[0].instrument_spec.ccxt_symbol, "XRP/USDT:USDT")


if __name__ == "__main__":
    unittest.main()
