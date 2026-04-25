from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from nautilus_app.config import load_settings
from nautilus_app.runners.backtest import build_demo_components, generate_synthetic_bars
from nautilus_app.runners.backtest import run_backtest


class BacktestRunnerTest(unittest.TestCase):
    def test_generate_synthetic_bars_uses_configured_count(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        components = build_demo_components(settings)

        bars = generate_synthetic_bars(
            settings=settings,
            instrument=components.instrument,
            bar_type=components.bar_type,
        )

        self.assertEqual(len(bars), settings.bars_count)

    def test_build_demo_components_uses_strategy_periods_from_settings(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")

        components = build_demo_components(settings)

        self.assertEqual(
            components.strategy_config.fast_ema_period,
            settings.fast_ema_period,
        )
        self.assertEqual(
            components.strategy_config.slow_ema_period,
            settings.slow_ema_period,
        )

    def test_run_backtest_returns_html_report_path(self):
        result = run_backtest()

        self.assertIn("tearsheet_path", result)
        self.assertTrue(str(result["tearsheet_path"]).endswith(".html"))
        self.assertTrue(Path(result["tearsheet_path"]).exists())


if __name__ == "__main__":
    unittest.main()
