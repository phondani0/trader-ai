from __future__ import annotations

import os
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import ccxt
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.config import (
    BinanceDataClientConfig,
    BinanceExecClientConfig,
)
from nautilus_trader.adapters.binance.factories import (
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.adapters.bybit.config import (
    BybitDataClientConfig,
    BybitEnvironment,
    BybitExecClientConfig,
)
from nautilus_trader.adapters.bybit.factories import (
    BybitLiveDataClientFactory,
    BybitLiveExecClientFactory,
)
from nautilus_trader.config import (
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode

from trader_ai.config import load_settings
from trader_ai.config import load_strategy_configs
from trader_ai.core.database import DatabaseManager
from trader_ai.intelligence.journal import DiscoveryJournal
from trader_ai.intelligence.scanner import MarketScanner
from trader_ai.runners.backtest import BacktestComponents
from trader_ai.runners.backtest import build_backtest_components

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STRATEGIES_PATH = PROJECT_ROOT / "config" / "strategies.yaml"

# Global state for sidecars
_db: DatabaseManager | None = None
_journal: DiscoveryJournal | None = None
_scanner: MarketScanner | None = None


def _trigger_discovery_scan(_event: object | None = None) -> None:
    """Sidecar task: Run MarketScanner V2."""
    from loguru import logger

    if _scanner:
        logger.info("Running scheduled MarketScanner discovery...")
        _scanner.scan_for_momentum()


def _trigger_journal_review(_event: object | None = None) -> None:
    """Sidecar task: Run DiscoveryJournal audit."""
    from loguru import logger

    if _journal:
        logger.info("Running scheduled DiscoveryJournal review...")
        _journal.update_performance()


def build_live_components(
    settings,
    instrument_name: str | None = None,
    strategy_name: str | None = None,
    strategies_path: str | Path | None = None,
) -> list[BacktestComponents]:
    if instrument_name is not None or strategy_name is not None:
        return [
            build_backtest_components(
                settings,
                instrument_name=instrument_name,
                strategy_name=strategy_name,
                is_live=True,
                discovery_gate="soft",
                use_discovery_journal=False,
            )
        ]

    strategy_configs = load_strategy_configs(strategies_path or STRATEGIES_PATH)
    components: list[BacktestComponents] = []
    for resolved_strategy_name, strategy_config in strategy_configs.items():
        if not strategy_config.enabled:
            continue
        for resolved_instrument_name in strategy_config.instruments:
            components.append(
                build_backtest_components(
                    settings,
                    instrument_name=resolved_instrument_name,
                    strategy_name=resolved_strategy_name,
                    is_live=True,
                    discovery_gate=strategy_config.discovery_gate,
                    strategy_params=strategy_config.params,
                    use_discovery_journal=False,
                )
            )

    if not components:
        raise ValueError(f"No enabled live strategies found in {strategies_path or STRATEGIES_PATH}")

    venues = {component.venue.value for component in components}
    if len(venues) != 1:
        raise ValueError(
            "Live trading currently supports one venue per node; "
            f"configured venues: {sorted(venues)}"
        )

    return components


def build_live_node(
    settings_path: str | Path | None,
    instrument_name: str | None = None,
    strategy_name: str | None = None,
) -> TradingNode:
    """Build a TradingNode for live trading/paper trading via WebSocket."""
    settings = (
        load_settings(settings_path)
        if settings_path is not None
        else load_settings(PROJECT_ROOT / "config" / "settings.yaml")
    )

    # Reuse the same strategy/instrument builder as backtesting
    components = build_live_components(
        settings,
        instrument_name=instrument_name,
        strategy_name=strategy_name,
    )
    primary_component = components[0]

    # Initialize the Sidecars
    global _db, _journal, _scanner
    if _db is None:
        _db = DatabaseManager()
        exchange_name = primary_component.instrument_spec.ccxt_exchange or "binanceusdm"
        exchange_class = getattr(ccxt, exchange_name)
        exchange = exchange_class()
        _journal = DiscoveryJournal(exchange, _db, asdict(settings))
        _scanner = MarketScanner(exchange, asdict(settings), _journal)

    is_paper = os.environ.get("TRADING_MODE", "paper").lower() == "paper"
    api_key = os.environ.get("EXCHANGE_API_KEY", "")
    api_secret = os.environ.get("EXCHANGE_SECRET_KEY", "")

    venue_str = primary_component.venue.value.upper()
    data_clients = {}
    exec_clients = {}

    if venue_str == "BINANCE":
        # Check environment for URL overrides, fallback to defaults
        base_uri = os.environ.get(
            "EXCHANGE_WSS_URL",
            "wss://stream.binancefuture.com"
            if is_paper
            else "wss://fstream.binance.com",
        )
        api_url = os.environ.get(
            "EXCHANGE_REST_URL",
            "https://testnet.binancefuture.com"
            if is_paper
            else "https://fapi.binance.com",
        )

        data_clients["BINANCE"] = BinanceDataClientConfig(
            base_url_ws=base_uri,
            account_type=BinanceAccountType.USDT_FUTURES,
            api_key=api_key,
            api_secret=api_secret,
            testnet=is_paper,
        )
        exec_clients["BINANCE"] = BinanceExecClientConfig(
            base_url_http=api_url,
            account_type=BinanceAccountType.USDT_FUTURES,
            api_key=api_key,
            api_secret=api_secret,
            testnet=is_paper,
        )

    elif venue_str == "BYBIT":
        env = BybitEnvironment.DEMO if is_paper else BybitEnvironment.MAINNET

        data_clients["BYBIT"] = BybitDataClientConfig(
            api_key=api_key,
            api_secret=api_secret,
            environment=env,
        )
        exec_clients["BYBIT"] = BybitExecClientConfig(
            api_key=api_key,
            api_secret=api_secret,
            environment=env,
        )
    else:
        raise ValueError(f"Unsupported venue for live trading: {venue_str}")

    # 1. Configure the TradingNode
    node_config = TradingNodeConfig(
        data_engine=LiveDataEngineConfig(),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
        ),
        risk_engine=LiveRiskEngineConfig(),
        data_clients=data_clients,
        exec_clients=exec_clients,
    )
    node = TradingNode(config=node_config)

    # 2. Add factories
    if venue_str == "BINANCE":
        node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
        node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)
    elif venue_str == "BYBIT":
        node.add_data_client_factory("BYBIT", BybitLiveDataClientFactory)
        node.add_exec_client_factory("BYBIT", BybitLiveExecClientFactory)

    # 3. Build node components
    node.build()

    # 4. Add the Strategy
    for component in components:
        node.trader.add_strategy(component.strategy)

    # 5. Wire the sidecar tasks (Scanner and Journal) to the Node's Clock
    discovery_interval = getattr(settings, "discovery_interval", 10)
    node.kernel.clock.set_timer(
        name="discovery_scan",
        interval=timedelta(minutes=discovery_interval),
        callback=_trigger_discovery_scan,
    )

    node.kernel.clock.set_timer(
        name="journal_review",
        interval=timedelta(minutes=30),
        callback=_trigger_journal_review,
    )

    return node


def run_live(
    settings_path: str | Path | None,
    instrument_name: str | None = None,
    strategy_name: str | None = None,
):
    """Run the TradingNode infinitely."""
    node = build_live_node(
        settings_path=settings_path,
        instrument_name=instrument_name,
        strategy_name=strategy_name,
    )

    # Block forever while asyncio loop handles WebSocket events and timers
    node.run()
