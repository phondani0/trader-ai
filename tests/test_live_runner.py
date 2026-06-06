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
from trader_ai.cli import live as live_cli  # noqa: E402
from trader_ai.config import StrategyExecutionConfig  # noqa: E402


class LiveRunnerTimerCallbackTest(unittest.TestCase):
    def test_live_cli_configures_observability_before_run_live(self):
        with (
            patch.object(
                sys,
                "argv",
                ["start", "--settings", str(PROJECT_ROOT / "config" / "settings.yaml")],
            ),
            patch.object(live_cli, "load_runtime_env") as load_runtime_env,
            patch.object(live_cli, "configure_observability") as configure,
            patch.object(live_cli, "run_live") as run_live,
        ):
            live_cli.main()

        load_runtime_env.assert_called_once_with()
        configure.assert_called_once_with()
        run_live.assert_called_once_with(
            settings_path=str(PROJECT_ROOT / "config" / "settings.yaml"),
            instrument_name=None,
            strategy_name=None,
        )

    def test_discovery_timer_callback_accepts_nautilus_time_event(self):
        scanner = Mock()

        with patch.object(live, "_scanner", scanner):
            live._trigger_discovery_scan(object())

        scanner.scan_for_momentum.assert_called_once_with()

    def test_discovery_timer_records_metrics_and_span(self):
        scanner = Mock()
        scanner.scan_for_momentum.return_value = []

        with (
            patch.object(live, "_scanner", scanner),
            patch.object(live, "_runtime_node", None),
            patch.object(live, "metrics") as metrics,
            patch.object(live, "tracer") as tracer,
        ):
            live._trigger_discovery_scan(object())

        metrics.increment.assert_called_once_with(
            "trader_scanner_runs_total",
            attributes={"result": "success"},
        )
        metrics.record.assert_called_once()
        self.assertEqual(
            metrics.record.call_args.args[0],
            "trader_scanner_duration_seconds",
        )
        tracer.start_as_current_span.assert_called_once_with("scanner.discovery")

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
                ("volume_spike_trend", "ETH/USDT:USDT"),
                ("squeeze_breakout", "BTC/USDT:USDT"),
                ("demo_ema_cross", "XRP/USDT:USDT"),
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

    def test_build_live_client_configs_loads_all_bybit_instruments(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        components = live.build_live_components(settings)

        data_clients, _ = live.build_live_client_configs(
            components,
            is_paper=True,
            api_key="key",
            api_secret="secret",
        )

        bybit_config = data_clients["BYBIT"]
        self.assertTrue(bybit_config.instrument_provider.load_all)
        self.assertIsNone(bybit_config.instrument_provider.load_ids)

    def test_build_dynamic_bybit_component_supports_scanner_symbol(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")

        component = live.build_dynamic_bybit_component(
            settings,
            strategy_name="adaptive_trend_rider",
            ccxt_symbol="NEAR/USDT:USDT",
            strategy_params={"fast_ema_period": 10, "slow_ema_period": 20},
            discovery_gate="hard",
        )

        self.assertEqual(str(component.instrument.id), "NEARUSDT-LINEAR.BYBIT")
        self.assertEqual(component.instrument_name, "nearusdt_linear_bybit")
        self.assertEqual(component.instrument_spec.ccxt_symbol, "NEAR/USDT:USDT")
        self.assertEqual(component.strategy.config.discovery_gate, "hard")

    def test_build_live_components_includes_scanner_candidates(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        candidates = [
            {"symbol": "NEAR/USDT:USDT", "score": 14.0, "volume_24h": 340_000_000},
            {"symbol": "BTC/USDT:USDT", "score": 13.0, "volume_24h": 4_000_000_000},
            {"symbol": "THIN/USDT:USDT", "score": 20.0, "volume_24h": 1_000_000},
        ]

        components = live.build_live_components(
            settings,
            scanner_candidates=candidates,
        )

        configured_components = live.build_live_components(settings)
        configured_btc_count = [
            component.instrument_spec.ccxt_symbol for component in configured_components
        ].count("BTC/USDT:USDT")
        symbols = [component.instrument_spec.ccxt_symbol for component in components]
        self.assertIn("NEAR/USDT:USDT", symbols)
        self.assertEqual(symbols.count("BTC/USDT:USDT"), configured_btc_count)
        self.assertNotIn("THIN/USDT:USDT", symbols)

    def test_build_startup_live_components_uses_scanner_snapshot(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        scanner = Mock()
        scanner.scan_for_momentum.return_value = [
            {"symbol": "NEAR/USDT:USDT", "score": 14.0, "volume_24h": 340_000_000},
        ]
        scanner.get_dynamic_universe_symbols.return_value = []

        components = live.build_startup_live_components(settings, scanner=scanner)

        symbols = [component.instrument_spec.ccxt_symbol for component in components]
        self.assertIn("NEAR/USDT:USDT", symbols)
        scanner.scan_for_momentum.assert_called_once_with()

    def test_build_startup_live_components_preloads_runtime_universe(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        scanner = Mock()
        scanner.scan_for_momentum.return_value = [
            {"symbol": "BSB/USDT:USDT", "score": 14.0, "volume_24h": 340_000_000},
        ]
        scanner.get_dynamic_universe_symbols.return_value = [
            "NEAR/USDT:USDT",
            "BILL/USDT:USDT",
            "TON/USDT:USDT",
            "SUI/USDT:USDT",
            "DOGE/USDT:USDT",
            "ADA/USDT:USDT",
            "XAU/USDT:USDT",
        ]

        components = live.build_startup_live_components(settings, scanner=scanner)

        symbols = [component.instrument_spec.ccxt_symbol for component in components]
        self.assertIn("BSB/USDT:USDT", symbols)
        self.assertIn("NEAR/USDT:USDT", symbols)
        self.assertIn("BILL/USDT:USDT", symbols)
        self.assertIn("TON/USDT:USDT", symbols)
        self.assertIn("SUI/USDT:USDT", symbols)
        self.assertIn("DOGE/USDT:USDT", symbols)
        self.assertIn("ADA/USDT:USDT", symbols)
        self.assertNotIn("XAU/USDT:USDT", symbols)

    def test_activate_runtime_scanner_symbols_does_not_add_strategy_to_running_node(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        scanner = Mock()
        scanner.scan_for_momentum.return_value = [
            {"symbol": "NEAR/USDT:USDT", "score": 14.0, "volume_24h": 340_000_000},
            {"symbol": "BTC/USDT:USDT", "score": 13.0, "volume_24h": 4_000_000_000},
        ]
        node = Mock()
        active_symbols = {
            "BTC/USDT:USDT",
            "ETH/USDT:USDT",
            "XRP/USDT:USDT",
            "NEAR/USDT:USDT",
        }

        components = live.activate_runtime_scanner_symbols(
            settings=settings,
            scanner=scanner,
            node=node,
            active_symbols=active_symbols,
            cache_dir=PROJECT_ROOT / "data" / "cache",
        )

        self.assertEqual(components, [])
        node.trader.add_strategy.assert_not_called()

    def test_activate_runtime_scanner_symbols_activates_unpreloaded_symbol(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        scanner = Mock()
        scanner.scan_for_momentum.return_value = [
            {"symbol": "FARTCOIN/USDT:USDT", "score": 14.0, "volume_24h": 340_000_000},
        ]
        node = Mock()
        runtime_strategy = Mock()
        runtime_strategy.active_ccxt_symbols.return_value = {
            "BTC/USDT:USDT",
            "ETH/USDT:USDT",
            "XRP/USDT:USDT",
        }
        loader = Mock(return_value=[])
        active_symbols = set(runtime_strategy.active_ccxt_symbols.return_value)

        components = live.activate_runtime_scanner_symbols(
            settings=settings,
            scanner=scanner,
            node=node,
            active_symbols=active_symbols,
            cache_dir=PROJECT_ROOT / "data" / "cache",
            runtime_strategy=runtime_strategy,
            warmer=loader,
        )

        self.assertEqual(len(components), 1)
        self.assertEqual(components[0].instrument_spec.ccxt_symbol, "FARTCOIN/USDT:USDT")
        runtime_strategy.activate_component.assert_called_once()
        node.trader.add_strategy.assert_not_called()
        self.assertIn("FARTCOIN/USDT:USDT", active_symbols)

    def test_activate_runtime_scanner_symbols_waits_for_running_node(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        scanner = Mock()
        scanner.scan_for_momentum.return_value = [
            {"symbol": "FARTCOIN/USDT:USDT", "score": 14.0, "volume_24h": 340_000_000},
        ]
        node = Mock()
        node.is_running = False
        runtime_strategy = Mock()

        components = live.activate_runtime_scanner_symbols(
            settings=settings,
            scanner=scanner,
            node=node,
            active_symbols={"BTC/USDT:USDT"},
            runtime_strategy=runtime_strategy,
        )

        self.assertEqual(components, [])
        runtime_strategy.activate_component.assert_not_called()

    def test_activate_runtime_scanner_symbols_routes_to_matching_strategy(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        scanner = Mock()
        scanner.scan_for_momentum.return_value = [
            {"symbol": "FARTCOIN/USDT:USDT", "score": 14.0, "volume_24h": 340_000_000},
        ]
        node = Mock()
        node.is_running = True
        adaptive_strategy = Mock()
        volume_strategy = Mock()
        loader = Mock(return_value=[])
        active_symbols = {"BTC/USDT:USDT"}
        configs = {
            "adaptive_trend_rider": StrategyExecutionConfig(
                enabled=True,
                instruments=[],
                discovery_gate="hard",
                params={"trade_notional_usdt": "150"},
                scanner_execution={
                    "enabled": False,
                    "max_symbols": 0,
                    "min_score": 10.0,
                    "min_24h_volume": 50_000_000.0,
                    "blacklist": [],
                    "discovery_gate": "hard",
                },
            ),
            "volume_spike_trend": StrategyExecutionConfig(
                enabled=True,
                instruments=[],
                discovery_gate="hard",
                params={"trade_notional_usdt": "150"},
                scanner_execution={
                    "enabled": True,
                    "max_symbols": 1,
                    "min_score": 10.0,
                    "min_24h_volume": 50_000_000.0,
                    "blacklist": [],
                    "discovery_gate": "hard",
                },
            ),
        }

        with patch.object(live, "load_strategy_configs", return_value=configs):
            components = live.activate_runtime_scanner_symbols(
                settings=settings,
                scanner=scanner,
                node=node,
                active_symbols=active_symbols,
                cache_dir=PROJECT_ROOT / "data" / "cache",
                runtime_strategies={
                    "adaptive_trend_rider": adaptive_strategy,
                    "volume_spike_trend": volume_strategy,
                },
                warmer=loader,
            )

        self.assertEqual(len(components), 1)
        self.assertEqual(components[0].strategy_name, "volume_spike_trend")
        adaptive_strategy.activate_component.assert_not_called()
        volume_strategy.activate_component.assert_called_once()

    def test_activate_runtime_scanner_symbols_records_activation_metric(self):
        settings = live.load_settings(PROJECT_ROOT / "config" / "settings.yaml")
        scanner = Mock()
        scanner.scan_for_momentum.return_value = [
            {"symbol": "FARTCOIN/USDT:USDT", "score": 14.0, "volume_24h": 340_000_000},
        ]
        node = Mock()
        node.is_running = True
        runtime_strategy = Mock()
        loader = Mock(return_value=[])
        active_symbols = {"BTC/USDT:USDT"}

        with (
            patch.object(live, "metrics") as metrics,
            patch.object(live, "tracer") as tracer,
        ):
            components = live.activate_runtime_scanner_symbols(
                settings=settings,
                scanner=scanner,
                node=node,
                active_symbols=active_symbols,
                cache_dir=PROJECT_ROOT / "data" / "cache",
                runtime_strategy=runtime_strategy,
                warmer=loader,
            )

        self.assertEqual(len(components), 1)
        metrics.increment.assert_called_with(
            "trader_runtime_activations_total",
            attributes={
                "result": "success",
                "strategy": "adaptive_trend_rider",
                "symbol": "FARTCOIN/USDT:USDT",
            },
        )
        tracer.start_as_current_span.assert_called()


if __name__ == "__main__":
    unittest.main()
