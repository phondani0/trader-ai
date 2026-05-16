from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.config import load_settings
from trader_ai.runners.backtest import _parse_balance
from trader_ai.runners.backtest import _parse_balance_currency
from trader_ai.runners.backtest import build_backtest_components
from trader_ai.runners.backtest import run_backtest
from trader_ai.data import generate_synthetic_bars


class BacktestRunnerTest(unittest.TestCase):
    def test_generate_synthetic_bars_uses_configured_count(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        components = build_backtest_components(settings)

        bars = generate_synthetic_bars(
            settings=settings,
            instrument=components.instrument,
            bar_type=components.bar_type,
        )

        self.assertEqual(len(bars), settings.bars_count)

    def test_build_demo_components_uses_strategy_periods_from_settings(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")

        components = build_backtest_components(settings)

        self.assertEqual(
            components.strategy_config.fast_ema_period,
            settings.fast_ema_period,
        )
        self.assertEqual(
            components.strategy_config.slow_ema_period,
            settings.slow_ema_period,
        )

    def test_run_backtest_returns_html_report_path(self):
        with TemporaryDirectory() as tmpdir:
            result = run_backtest(output_dir=tmpdir, data_source="synthetic")
            self.assertIn("tearsheet_path", result)
            self.assertTrue(str(result["tearsheet_path"]).endswith(".html"))
            self.assertTrue(Path(result["tearsheet_path"]).exists())
            self.assertTrue(Path(result["summary_path"]).exists())
            self.assertTrue(Path(result["positions_path"]).exists())
            self.assertTrue(Path(result["fills_path"]).exists())
            self.assertTrue(Path(result["account_path"]).exists())
            self.assertTrue(Path(result["monthly_returns_path"]).exists())

            summary = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["strategy"], "adaptive_trend_rider")
            self.assertEqual(summary["data_source"], "synthetic")
            self.assertIn("instrument_id", summary)
            self.assertIn("monthly_returns_path", summary)

    def test_build_backtest_components_accepts_instrument_and_strategy_override(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")

        components = build_backtest_components(
            settings,
            instrument_name="btcusdt_perp_binance",
            strategy_name="nautilus_ema_cross",
        )

        self.assertEqual(components.strategy_name, "nautilus_ema_cross")
        self.assertEqual(str(components.instrument.id), "BTCUSDT-PERP.BINANCE")
        self.assertEqual(
            str(components.bar_type),
            "BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL",
        )

    def test_balance_parser_supports_usdt_balances(self):
        balance = _parse_balance("1000 USDT")
        currency = _parse_balance_currency("1000 USDT")

        self.assertEqual(str(balance), "1000.00000000 USDT")
        self.assertEqual(str(currency), "USDT")


if __name__ == "__main__":
    unittest.main()
