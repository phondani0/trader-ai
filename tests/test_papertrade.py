from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.runners.papertrade import build_papertrade_plan
from trader_ai.runners.papertrade import build_runtime_template


class PapertradeTest(unittest.TestCase):
    def test_build_papertrade_plan_returns_resolved_bootstrap_manifest(self):
        plan = build_papertrade_plan(
            settings_path=PROJECT_ROOT / "config" / "settings.yaml",
            instrument_name="btcusdt_perp_binance",
            strategy_name="nautilus_ema_cross",
            data_source="ccxt",
        )

        self.assertEqual(plan["mode"], "papertrade-bootstrap")
        self.assertEqual(plan["instrument_name"], "btcusdt_perp_binance")
        self.assertEqual(plan["strategy"], "nautilus_ema_cross")
        self.assertEqual(plan["data_source"], "ccxt")
        self.assertEqual(plan["ccxt_exchange"], "binanceusdm")

    def test_build_runtime_template_mentions_exchange_env_vars(self):
        template = build_runtime_template()

        self.assertIn("EXCHANGE_API_KEY", template)
        self.assertIn("EXCHANGE_SECRET_KEY", template)
        self.assertIn("TRADING_MODE=paper", template)


if __name__ == "__main__":
    unittest.main()
