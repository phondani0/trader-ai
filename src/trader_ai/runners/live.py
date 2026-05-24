from __future__ import annotations

import os
import time
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
    InstrumentProviderConfig,
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode

from trader_ai.config import load_settings
from trader_ai.config import load_strategy_configs
from trader_ai.core.database import DatabaseManager
from trader_ai.data import load_ccxt_bars
from trader_ai.intelligence.journal import DiscoveryJournal
from trader_ai.intelligence.scanner import MarketScanner
from trader_ai.runners.backtest import BacktestComponents
from trader_ai.runners.backtest import build_backtest_components
from trader_ai.runners.backtest import build_dynamic_bybit_linear_components
from trader_ai.runners.scanner_execution import ScannerExecutionConfig
from trader_ai.runners.scanner_execution import select_scanner_execution_symbols

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STRATEGIES_PATH = PROJECT_ROOT / "config" / "strategies.yaml"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache"

# Global state for sidecars
_db: DatabaseManager | None = None
_journal: DiscoveryJournal | None = None
_scanner: MarketScanner | None = None
_runtime_node: TradingNode | None = None
_runtime_settings = None
_runtime_symbols: set[str] = set()


def _trigger_discovery_scan(_event: object | None = None) -> None:
    """Sidecar task: Run MarketScanner V2."""
    from loguru import logger

    if _scanner:
        logger.info("Running scheduled MarketScanner discovery...")
        candidates = _scanner.scan_for_momentum()
        if _runtime_node is not None and _runtime_settings is not None:
            activate_runtime_scanner_symbols(
                settings=_runtime_settings,
                scanner=_scanner,
                node=_runtime_node,
                active_symbols=_runtime_symbols,
                candidates=candidates,
            )


def _trigger_journal_review(_event: object | None = None) -> None:
    """Sidecar task: Run DiscoveryJournal audit."""
    from loguru import logger

    if _journal:
        logger.info("Running scheduled DiscoveryJournal review...")
        _journal.update_performance()


def build_live_client_configs(
    components: list[BacktestComponents],
    *,
    is_paper: bool,
    api_key: str,
    api_secret: str,
):
    primary_component = components[0]
    venue_str = primary_component.venue.value.upper()
    data_clients = {}
    exec_clients = {}

    if venue_str == "BINANCE":
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
            instrument_provider=InstrumentProviderConfig(load_all=True),
        )
        exec_clients["BYBIT"] = BybitExecClientConfig(
            api_key=api_key,
            api_secret=api_secret,
            environment=env,
        )
    else:
        raise ValueError(f"Unsupported venue for live trading: {venue_str}")

    return data_clients, exec_clients


def build_live_components(
    settings,
    instrument_name: str | None = None,
    strategy_name: str | None = None,
    strategies_path: str | Path | None = None,
    scanner_candidates: list[dict] | None = None,
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

    configured_symbols = component_ccxt_symbols(components)
    components.extend(
        build_scanner_live_components(
            settings,
            scanner_candidates=scanner_candidates or [],
            configured_symbols=configured_symbols,
            strategies_path=strategies_path,
        )
    )

    venues = {component.venue.value for component in components}
    if len(venues) != 1:
        raise ValueError(
            "Live trading currently supports one venue per node; "
            f"configured venues: {sorted(venues)}"
        )

    return components


def component_ccxt_symbols(components: list[BacktestComponents]) -> set[str]:
    return {
        component.instrument_spec.ccxt_symbol
        for component in components
        if component.instrument_spec.ccxt_symbol is not None
    }


def build_scanner_live_components(
    settings,
    *,
    scanner_candidates: list[dict],
    configured_symbols: set[str],
    strategies_path: str | Path | None = None,
) -> list[BacktestComponents]:
    strategy_configs = load_strategy_configs(strategies_path or STRATEGIES_PATH)
    components: list[BacktestComponents] = []
    seen_symbols = set(configured_symbols)

    for resolved_strategy_name, strategy_config in strategy_configs.items():
        if not strategy_config.enabled:
            continue

        scanner_config = strategy_config.scanner_execution
        selected_symbols = select_scanner_execution_symbols(
            scanner_candidates,
            configured_symbols=seen_symbols,
            config=ScannerExecutionConfig.from_mapping(scanner_config),
        )
        for symbol in selected_symbols:
            components.append(
                build_dynamic_bybit_component(
                    settings,
                    strategy_name=resolved_strategy_name,
                    ccxt_symbol=symbol,
                    strategy_params=strategy_config.params,
                    discovery_gate=str(scanner_config.get("discovery_gate", "hard")),
                )
            )
            seen_symbols.add(symbol)

    return components


def build_startup_live_components(settings, *, scanner=None) -> list[BacktestComponents]:
    if scanner is None:
        return build_live_components(settings)

    from loguru import logger

    try:
        candidates = scanner.scan_for_momentum()
    except Exception as exc:
        logger.warning(f"Startup scanner snapshot failed: {exc}")
        candidates = []

    return build_live_components(settings, scanner_candidates=candidates)


def activate_runtime_scanner_symbols(
    *,
    settings,
    scanner,
    node,
    active_symbols: set[str],
    candidates: list[dict] | None = None,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    warmer=None,
) -> list[BacktestComponents]:
    from loguru import logger

    if candidates is None:
        candidates = scanner.scan_for_momentum()

    components = build_scanner_live_components(
        settings,
        scanner_candidates=candidates,
        configured_symbols=active_symbols,
    )
    if not components:
        return []

    if settings.warmup_enabled:
        resolved_warmer = warmup_live_components if warmer is None else warmer
        resolved_warmer(
            components,
            cache_dir=cache_dir,
            bars_limit=settings.warmup_bars,
        )

    for component in components:
        node.trader.add_strategy(component.strategy)
        if component.instrument_spec.ccxt_symbol:
            active_symbols.add(component.instrument_spec.ccxt_symbol)
        logger.info(
            "Runtime scanner activated "
            f"{component.strategy_name}/{component.instrument_name}"
        )

    return components


def build_dynamic_bybit_component(
    settings,
    *,
    strategy_name: str,
    ccxt_symbol: str,
    strategy_params: dict[str, object],
    discovery_gate: str,
) -> BacktestComponents:
    return build_dynamic_bybit_linear_components(
        settings,
        ccxt_symbol=ccxt_symbol,
        strategy_name=strategy_name,
        strategy_params=strategy_params,
        discovery_gate=discovery_gate,
    )


def warmup_live_components(
    components: list[BacktestComponents],
    *,
    cache_dir: str | Path,
    bars_limit: int,
    loader=load_ccxt_bars,
    now_ns: int | None = None,
) -> list[dict[str, object]]:
    from loguru import logger

    resolved_now_ns = time.time_ns() if now_ns is None else now_ns
    reports: list[dict[str, object]] = []
    for component in components:
        try:
            bars = loader(
                cache_dir=cache_dir,
                instrument_name=component.instrument_name,
                instrument_spec=component.instrument_spec,
                instrument=component.instrument,
                bar_type=component.bar_type,
                limit=bars_limit,
            )
            timeframe_ns = _timeframe_to_ns(component.instrument_spec.ccxt_timeframe)
            bars = _closed_warmup_bars(
                bars,
                timeframe_ns=timeframe_ns,
                now_ns=resolved_now_ns,
            )
            last_replayed_close_ns = (
                bars[-1].ts_event + timeframe_ns if bars else None
            )
            seed_warmup = getattr(
                component.strategy,
                "seed_warmup",
                None,
            )
            if not callable(seed_warmup):
                reports.append(
                    {
                        "instrument": component.instrument_name,
                        "strategy": component.strategy_name,
                        "bars": len(bars),
                        "ready": False,
                        "error": "strategy does not support warmup",
                    }
                )
                logger.warning(
                    "Warmup skipped for "
                    f"{component.strategy_name}/{component.instrument_name}: "
                    "strategy does not support warmup"
                )
                continue

            seed_warmup(
                bars,
                last_replayed_close_ns=last_replayed_close_ns,
            )
            ready = (
                bool(component.strategy._indicators_ready())
                if hasattr(component.strategy, "_indicators_ready")
                else False
            )
            reports.append(
                {
                    "instrument": component.instrument_name,
                    "strategy": component.strategy_name,
                    "bars": len(bars),
                    "ready": ready,
                    "error": None,
                }
            )
            logger.info(
                "Warmup replayed "
                f"{len(bars)} bars for "
                f"{component.strategy_name}/{component.instrument_name}; "
                f"ready={ready}"
            )
        except Exception as exc:
            reports.append(
                {
                    "instrument": component.instrument_name,
                    "strategy": component.strategy_name,
                    "bars": 0,
                    "ready": False,
                    "error": str(exc),
                }
            )
            logger.warning(
                "Warmup failed for "
                f"{component.strategy_name}/{component.instrument_name}: {exc}"
            )

    return reports


def _closed_warmup_bars(
    bars: list,
    *,
    timeframe_ns: int,
    now_ns: int,
) -> list:
    return [bar for bar in bars if bar.ts_event + timeframe_ns <= now_ns]


def _timeframe_to_ns(raw_timeframe: str | None) -> int:
    if not raw_timeframe:
        return 15 * 60 * 1_000_000_000

    unit = raw_timeframe[-1].lower()
    amount = int(raw_timeframe[:-1])
    multipliers = {
        "s": 1_000_000_000,
        "m": 60 * 1_000_000_000,
        "h": 60 * 60 * 1_000_000_000,
        "d": 24 * 60 * 60 * 1_000_000_000,
    }
    if unit not in multipliers:
        raise ValueError(f"Unsupported CCXT timeframe: {raw_timeframe}")
    return amount * multipliers[unit]


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

    # Reuse the same strategy/instrument builder as backtesting.
    initial_components = build_live_components(
        settings,
        instrument_name=instrument_name,
        strategy_name=strategy_name,
    )
    primary_component = initial_components[0]

    # Initialize the Sidecars
    global _db, _journal, _scanner
    if _db is None:
        _db = DatabaseManager()
        exchange_name = primary_component.instrument_spec.ccxt_exchange or "binanceusdm"
        exchange_class = getattr(ccxt, exchange_name)
        exchange = exchange_class()
        _journal = DiscoveryJournal(exchange, _db, asdict(settings))
        _scanner = MarketScanner(exchange, asdict(settings), _journal)

    components = (
        initial_components
        if instrument_name is not None or strategy_name is not None
        else build_startup_live_components(settings, scanner=_scanner)
    )

    is_paper = os.environ.get("TRADING_MODE", "paper").lower() == "paper"
    api_key = os.environ.get("EXCHANGE_API_KEY", "")
    api_secret = os.environ.get("EXCHANGE_SECRET_KEY", "")

    venue_str = primary_component.venue.value.upper()
    data_clients, exec_clients = build_live_client_configs(
        components,
        is_paper=is_paper,
        api_key=api_key,
        api_secret=api_secret,
    )

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

    if settings.warmup_enabled:
        warmup_live_components(
            components,
            cache_dir=DEFAULT_CACHE_DIR,
            bars_limit=settings.warmup_bars,
        )

    global _runtime_node, _runtime_settings, _runtime_symbols
    _runtime_node = node
    _runtime_settings = settings
    _runtime_symbols = component_ccxt_symbols(components)

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
