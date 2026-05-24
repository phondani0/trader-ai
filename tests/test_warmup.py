from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.config import load_settings  # noqa: E402
from trader_ai.data import generate_synthetic_bars  # noqa: E402
from trader_ai.runners import live  # noqa: E402
from trader_ai.runners.backtest import build_backtest_components  # noqa: E402


class WarmupTest(unittest.TestCase):
    def test_adaptive_strategy_warmup_replays_bars_without_entry_evaluation(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        component = build_backtest_components(
            settings,
            instrument_name="xrpusdt_linear_bybit",
            strategy_name="adaptive_trend_rider",
            strategy_params={
                "fast_ema_period": 3,
                "slow_ema_period": 5,
                "rsi_period": 3,
                "atr_period": 3,
                "volume_window": 3,
                "atr_ma_period": 3,
            },
        )
        bars = generate_synthetic_bars(
            settings=settings,
            instrument=component.instrument,
            bar_type=component.bar_type,
        )[:30]
        component.strategy._evaluate_entry = Mock()

        component.strategy.seed_warmup(bars)

        self.assertTrue(component.strategy._indicators_ready())
        self.assertEqual(component.strategy._bars_seen, len(bars))
        component.strategy._evaluate_entry.assert_not_called()

    def test_adaptive_strategy_skips_live_bar_already_covered_by_warmup(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        component = build_backtest_components(
            settings,
            instrument_name="xrpusdt_linear_bybit",
            strategy_name="adaptive_trend_rider",
            strategy_params={
                "fast_ema_period": 3,
                "slow_ema_period": 5,
                "rsi_period": 3,
                "atr_period": 3,
                "volume_window": 3,
                "atr_ma_period": 3,
            },
        )
        bars = generate_synthetic_bars(
            settings=settings,
            instrument=component.instrument,
            bar_type=component.bar_type,
        )[:30]
        duplicate_bar = bars[-1]
        component.strategy.seed_warmup(
            bars,
            last_replayed_close_ns=duplicate_bar.ts_event,
        )
        bars_seen = component.strategy._bars_seen

        component.strategy.on_bar(duplicate_bar)

        self.assertEqual(component.strategy._bars_seen, bars_seen)

    def test_volume_strategy_warmup_replays_bars_without_entry_evaluation(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        component = build_backtest_components(
            settings,
            instrument_name="xrpusdt_linear_bybit",
            strategy_name="volume_spike_trend",
            strategy_params={
                "fast_ema_period": 3,
                "slow_ema_period": 5,
                "rsi_period": 3,
                "atr_period": 3,
                "volume_window": 3,
            },
        )
        bars = generate_synthetic_bars(
            settings=settings,
            instrument=component.instrument,
            bar_type=component.bar_type,
        )[:30]
        component.strategy._evaluate_entry = Mock()

        component.strategy.seed_warmup(bars)

        self.assertTrue(component.strategy._indicators_ready())
        self.assertEqual(component.strategy._bars_seen, len(bars))
        component.strategy._evaluate_entry.assert_not_called()

    def test_warmup_replays_loaded_bars_for_each_component(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        components = live.build_live_components(
            settings,
            instrument_name="xrpusdt_linear_bybit",
            strategy_name="adaptive_trend_rider",
        )
        bars = generate_synthetic_bars(
            settings=settings,
            instrument=components[0].instrument,
            bar_type=components[0].bar_type,
        )[:5]
        loader = Mock(return_value=bars)
        components[0].strategy.seed_warmup = Mock()

        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = live.warmup_live_components(
                components,
                cache_dir=tmp_dir,
                bars_limit=5,
                loader=loader,
            )

        loader.assert_called_once()
        components[0].strategy.seed_warmup.assert_called_once_with(
            bars,
            last_replayed_close_ns=bars[-1].ts_event + 15 * 60 * 1_000_000_000,
        )
        self.assertEqual(reports[0]["instrument"], "xrpusdt_linear_bybit")
        self.assertEqual(reports[0]["bars"], 5)
        self.assertFalse(reports[0]["ready"])
        self.assertIsNone(reports[0]["error"])

    def test_warmup_replays_only_closed_bars(self):
        settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        components = live.build_live_components(
            settings,
            instrument_name="xrpusdt_linear_bybit",
            strategy_name="adaptive_trend_rider",
        )
        bars = generate_synthetic_bars(
            settings=settings,
            instrument=components[0].instrument,
            bar_type=components[0].bar_type,
        )[:3]
        timeframe_ns = 15 * 60 * 1_000_000_000
        now_ns = bars[0].ts_event + timeframe_ns
        loader = Mock(return_value=bars)
        components[0].strategy.seed_warmup = Mock()

        with tempfile.TemporaryDirectory() as tmp_dir:
            reports = live.warmup_live_components(
                components,
                cache_dir=tmp_dir,
                bars_limit=3,
                loader=loader,
                now_ns=now_ns,
            )

        replayed_bars = components[0].strategy.seed_warmup.call_args.args[0]
        self.assertEqual(replayed_bars, bars[:1])
        self.assertEqual(reports[0]["bars"], 1)


if __name__ == "__main__":
    unittest.main()
