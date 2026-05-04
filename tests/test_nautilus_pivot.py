from __future__ import annotations

import unittest


from scripts.nautilus_dispatch import build_backtest_command
from scripts.nautilus_dispatch import build_benchmark_command
from scripts.nautilus_dispatch import build_start_command
from scripts.nautilus_dispatch import build_uv_run_command


class NautilusPivotDispatchTest(unittest.TestCase):
    def test_build_uv_run_command_keeps_passthrough_args(self):
        command = build_uv_run_command("nautilus-backtest", ["--instrument", "btcusdt_perp_binance"])

        self.assertEqual(
            command,
            ["uv", "run", "nautilus-backtest", "--instrument", "btcusdt_perp_binance"],
        )

    def test_build_backtest_command_uses_nautilus_backtest(self):
        command = build_backtest_command(["--strategy", "volume_spike_trend"])

        self.assertEqual(command[:3], ["uv", "run", "nautilus-backtest"])
        self.assertEqual(command[3:], ["--strategy", "volume_spike_trend"])

    def test_build_benchmark_command_uses_btc_core_defaults_when_no_args(self):
        command = build_benchmark_command([])

        self.assertEqual(command[:3], ["uv", "run", "nautilus-compare-strategies"])
        self.assertIn("--instrument", command)
        self.assertIn("btcusdt_perp_binance", command)
        self.assertIn("--strategies", command)
        self.assertIn("volume_spike_trend", command)
        self.assertIn("nautilus_ema_cross", command)
        self.assertIn("demo_ema_cross", command)

    def test_build_benchmark_command_passthrough_args_override_defaults(self):
        command = build_benchmark_command(
            ["--instrument", "ethusdt_perp_binance", "--strategies", "volume_spike_trend"]
        )

        self.assertEqual(
            command,
            [
                "uv",
                "run",
                "nautilus-compare-strategies",
                "--instrument",
                "ethusdt_perp_binance",
                "--strategies",
                "volume_spike_trend",
            ],
        )

    def test_build_start_command_uses_papertrade_defaults(self):
        command = build_start_command([])

        self.assertEqual(command[:3], ["uv", "run", "nautilus-papertrade"])
        self.assertIn("--instrument", command)
        self.assertIn("btcusdt_perp_binance", command)
        self.assertIn("--strategy", command)
        self.assertIn("volume_spike_trend", command)


if __name__ == "__main__":
    unittest.main()
