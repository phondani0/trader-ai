from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.cli import integration_smoke as integration_smoke_cli  # noqa: E402
from trader_ai.integration.smoke import build_bybit_demo_environment  # noqa: E402
from trader_ai.integration.smoke import run_local_sim_runtime_smoke  # noqa: E402


class IntegrationSmokeTest(unittest.TestCase):
    def test_local_sim_runtime_smoke_activates_all_runtime_strategies(self):
        report = run_local_sim_runtime_smoke(
            settings_path=PROJECT_ROOT / "config" / "settings.yaml",
        )

        self.assertTrue(report.ok)
        self.assertEqual(
            report.runtime_strategy_names,
            [
                "adaptive_trend_rider",
                "demo_ema_cross",
                "squeeze_breakout",
                "volume_spike_trend",
            ],
        )
        self.assertEqual(
            report.activated_symbols_by_strategy,
            {
                "adaptive_trend_rider": ["FARTCOIN/USDT:USDT"],
                "demo_ema_cross": ["SOL/USDT:USDT"],
                "squeeze_breakout": ["RENDER/USDT:USDT"],
                "volume_spike_trend": ["WLD/USDT:USDT"],
            },
        )
        self.assertEqual(report.failures, [])

    def test_bybit_demo_environment_forces_paper_and_refuses_live_mode(self):
        with patch.dict(os.environ, {"TRADING_MODE": "live"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "refuses TRADING_MODE=live"):
                build_bybit_demo_environment()

    def test_bybit_demo_environment_requires_credentials(self):
        with patch.dict(
            os.environ,
            {
                "TRADING_MODE": "paper",
                "EXCHANGE_API_KEY": "",
                "EXCHANGE_SECRET_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "requires EXCHANGE_API_KEY"):
                build_bybit_demo_environment()

    def test_bybit_demo_environment_sets_forced_demo_values(self):
        with patch.dict(
            os.environ,
            {
                "TRADING_MODE": "paper",
                "EXCHANGE_API_KEY": "demo_key",
                "EXCHANGE_SECRET_KEY": "demo_secret",
            },
            clear=False,
        ):
            env = build_bybit_demo_environment()

        self.assertEqual(env["TRADING_MODE"], "paper")
        self.assertEqual(env["EXCHANGE_ID"], "bybit")
        self.assertEqual(env["EXCHANGE_API_KEY"], "demo_key")
        self.assertEqual(env["EXCHANGE_SECRET_KEY"], "demo_secret")

    def test_cli_local_sim_mode_prints_activation_report(self):
        with patch.object(
            sys,
            "argv",
            [
                "integration-smoke",
                "--mode",
                "local-sim",
                "--settings",
                str(PROJECT_ROOT / "config" / "settings.yaml"),
            ],
        ):
            exit_code = integration_smoke_cli.main()

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
