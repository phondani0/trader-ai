from __future__ import annotations

import os
import time
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

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
from trader_ai.execution.recovery import ExchangePositionSnapshot
from trader_ai.execution.recovery import PositionRecoveryService
from trader_ai.execution.recovery import StrategyPositionState
from trader_ai.intelligence.journal import DiscoveryJournal
from trader_ai.intelligence.scanner import MarketScanner
from trader_ai.runners.backtest import BacktestComponents
from trader_ai.runners.backtest import build_backtest_components
from trader_ai.runners.backtest import build_dynamic_bybit_linear_components
from trader_ai.runners.scanner_execution import ScannerExecutionConfig
from trader_ai.runners.scanner_execution import select_scanner_execution_symbols
from trader_ai.observability import logger
from trader_ai.observability import metrics
from trader_ai.observability import tracer

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
_runtime_strategies: dict[str, object] = {}


def _trigger_discovery_scan(_event: object | None = None) -> None:
    """Sidecar task: Run MarketScanner V2."""
    if _scanner:
        started_at = time.perf_counter()
        result = "success"
        with tracer.start_as_current_span("scanner.discovery"):
            try:
                logger.info(
                    "Running scheduled MarketScanner discovery...",
                    log_type="scanner.discovery.started",
                )
                candidates = _scanner.scan_for_momentum()
                if _runtime_node is not None and _runtime_settings is not None:
                    activate_runtime_scanner_symbols(
                        settings=_runtime_settings,
                        scanner=_scanner,
                        node=_runtime_node,
                        active_symbols=_runtime_symbols,
                        candidates=candidates,
                        runtime_strategies=_runtime_strategies,
                    )
            except Exception:
                result = "error"
                raise
            finally:
                metrics.increment(
                    "trader_scanner_runs_total",
                    attributes={"result": result},
                )
                metrics.record(
                    "trader_scanner_duration_seconds",
                    time.perf_counter() - started_at,
                    attributes={"result": result},
                )


def _trigger_journal_review(_event: object | None = None) -> None:
    """Sidecar task: Run DiscoveryJournal audit."""
    if _journal:
        started_at = time.perf_counter()
        result = "success"
        with tracer.start_as_current_span("journal.review"):
            try:
                logger.info(
                    "Running scheduled DiscoveryJournal review...",
                    log_type="journal.review.started",
                )
                _journal.update_performance()
            except Exception:
                result = "error"
                raise
            finally:
                metrics.increment(
                    "trader_journal_reviews_total",
                    attributes={"result": result},
                )
                metrics.record(
                    "trader_journal_review_duration_seconds",
                    time.perf_counter() - started_at,
                    attributes={"result": result},
                )


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
    scanner_candidates = [candidate for candidate in scanner_candidates if candidate]
    startup_candidates = [
        candidate
        for candidate in scanner_candidates
        if not bool(candidate.get("runtime_preload"))
    ]
    runtime_preload_candidates = [
        candidate for candidate in scanner_candidates if bool(candidate.get("runtime_preload"))
    ]

    for resolved_strategy_name, strategy_config in strategy_configs.items():
        if not strategy_config.enabled:
            continue

        scanner_config = strategy_config.scanner_execution
        selected_symbols = [
            *select_scanner_execution_symbols(
                startup_candidates,
                configured_symbols=seen_symbols,
                config=ScannerExecutionConfig.from_mapping(scanner_config),
            ),
            *select_scanner_execution_symbols(
                runtime_preload_candidates,
                configured_symbols=seen_symbols,
                config=ScannerExecutionConfig.from_mapping(
                    {
                        **scanner_config,
                        "max_symbols": _runtime_preload_limit(scanner_config),
                    }
                ),
            ),
        ]
        selected_symbols = _dedupe_selected_symbols(
            selected_symbols,
            configured_symbols=seen_symbols,
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


def _runtime_preload_limit(scanner_config: dict[str, object]) -> int:
    if not bool(scanner_config.get("preload_runtime_symbols", False)):
        return 0
    return int(scanner_config.get("runtime_universe_size", 0))


def _dedupe_selected_symbols(
    symbols: list[str],
    *,
    configured_symbols: set[str],
) -> list[str]:
    selected: list[str] = []
    seen_symbols = set(configured_symbols)
    for symbol in symbols:
        if symbol in seen_symbols:
            continue
        selected.append(symbol)
        seen_symbols.add(symbol)
    return selected


def build_startup_live_components(
    settings, 
    *, 
    scanner=None, 
    db=None,
    exchange_positions=None,
) -> list[BacktestComponents]:
    if scanner is None:
        return build_live_components(settings)

    try:
        candidates = scanner.scan_for_momentum()
    except Exception as exc:
        logger.warning(
            "Startup scanner snapshot failed",
            log_type="scanner.startup.failed",
            error=str(exc),
        )
        candidates = []

    candidates = [
        *candidates,
        *build_runtime_preload_candidates(scanner),
    ]
    components = build_live_components(settings, scanner_candidates=candidates)

    symbols_to_inject = set()
    if db is not None:
        try:
            db_positions = db.get_open_strategy_positions()
            for pos in db_positions:
                symbols_to_inject.add((pos["symbol"], pos["strategy_name"]))
        except Exception:
            pass
            
    if exchange_positions is not None:
        strategy_configs = load_strategy_configs(STRATEGIES_PATH)
        # Attempt to map unmanaged positions to an active strategy
        active_strategies = [name for name, cfg in strategy_configs.items() if cfg.enabled]
        if active_strategies:
            default_strat = active_strategies[0] # Pick the first active one for adoption attempt
            for pos in exchange_positions:
                symbols_to_inject.add((pos.symbol, default_strat))

    if symbols_to_inject:
        try:
            configured_symbols = component_ccxt_symbols(components)
            strategy_configs = load_strategy_configs(STRATEGIES_PATH)
            for symbol, strategy_name in symbols_to_inject:
                if symbol not in configured_symbols and strategy_name in strategy_configs:
                    strat_cfg = strategy_configs[strategy_name]
                    if strat_cfg.enabled:
                        components.append(
                            build_dynamic_bybit_component(
                                settings,
                                strategy_name=strategy_name,
                                ccxt_symbol=symbol,
                                strategy_params=strat_cfg.params,
                                discovery_gate=str(strat_cfg.scanner_execution.get("discovery_gate", "hard")),
                            )
                        )
                        configured_symbols.add(symbol)
        except Exception as exc:
            logger.warning(
                "Failed to inject recovered components",
                log_type="recovery.inject.failed",
                error=str(exc)
            )

    return components


def build_runtime_preload_candidates(
    scanner,
    *,
    strategies_path: str | Path | None = None,
) -> list[dict[str, object]]:
    strategy_configs = load_strategy_configs(strategies_path or STRATEGIES_PATH)
    preload_limit = max(
        (
            int(config.scanner_execution.get("runtime_universe_size", 0))
            for config in strategy_configs.values()
            if config.enabled
            and bool(config.scanner_execution.get("preload_runtime_symbols", False))
        ),
        default=0,
    )
    if preload_limit <= 0:
        return []

    symbols = scanner.get_dynamic_universe_symbols()[:preload_limit]
    return [
        {
            "symbol": symbol,
            "score": float("inf"),
            "volume_24h": float("inf"),
            "runtime_preload": True,
        }
        for symbol in symbols
    ]


def activate_runtime_scanner_symbols(
    *,
    settings,
    scanner,
    node,
    active_symbols: set[str],
    candidates: list[dict] | None = None,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    warmer=None,
    runtime_strategy=None,
    runtime_strategies: dict[str, object] | None = None,
) -> list[BacktestComponents]:
    if candidates is None:
        candidates = scanner.scan_for_momentum()

    warmer = warmer or load_ccxt_bars
    tradable = [
        str(candidate.get("symbol"))
        for candidate in candidates
        if str(candidate.get("symbol")) in active_symbols
    ]
    if tradable:
        logger.info(
            "Runtime scanner discoveries already covered by preloaded strategies",
            log_type="runtime.activation.preloaded",
            symbols=", ".join(tradable),
        )

    strategy_map = dict(runtime_strategies or {})
    if runtime_strategy is not None:
        strategy_map.setdefault("adaptive_trend_rider", runtime_strategy)

    if not strategy_map:
        return []
    if not bool(getattr(node, "is_running", False)):
        logger.info(
            "Runtime scanner activation waiting for TradingNode RUNNING",
            log_type="runtime.activation.waiting",
        )
        return []

    strategy_configs = load_strategy_configs(STRATEGIES_PATH)
    activated_components: list[BacktestComponents] = []
    seen_symbols = set(active_symbols)
    for resolved_strategy_name, strategy_config in strategy_configs.items():
        if not strategy_config.enabled:
            continue
        runtime_strategy_for_name = strategy_map.get(resolved_strategy_name)
        if runtime_strategy_for_name is None:
            continue

        scanner_config = strategy_config.scanner_execution
        selected_symbols = select_scanner_execution_symbols(
            candidates,
            configured_symbols=seen_symbols,
            config=ScannerExecutionConfig.from_mapping(scanner_config),
        )
        for symbol in selected_symbols:
            activation_attributes = {
                "strategy": resolved_strategy_name,
                "symbol": symbol,
            }
            with tracer.start_as_current_span(
                "runtime.activation",
                attributes=activation_attributes,
            ):
                component = build_dynamic_bybit_component(
                    settings,
                    strategy_name=resolved_strategy_name,
                    ccxt_symbol=symbol,
                    strategy_params=strategy_config.params,
                    discovery_gate=str(scanner_config.get("discovery_gate", "hard")),
                )
                try:
                    bars = warmer(
                        cache_dir=cache_dir,
                        instrument_name=component.instrument_name,
                        instrument_spec=component.instrument_spec,
                        instrument=component.instrument,
                        bar_type=component.bar_type,
                        limit=settings.warmup_bars,
                    )
                    timeframe_ns = _timeframe_to_ns(
                        component.instrument_spec.ccxt_timeframe
                    )
                    bars = _closed_warmup_bars(
                        bars,
                        timeframe_ns=timeframe_ns,
                        now_ns=time.time_ns(),
                    )
                    last_replayed_close_ns = (
                        bars[-1].ts_event + timeframe_ns if bars else None
                    )
                    activated = runtime_strategy_for_name.activate_component(
                        component,
                        bars,
                        last_replayed_close_ns=last_replayed_close_ns,
                    )
                except Exception as exc:
                    logger.warning(
                        "Runtime scanner activation failed",
                        log_type="runtime.activation.failed",
                        error=str(exc),
                        **activation_attributes,
                    )
                    metrics.increment(
                        "trader_runtime_activations_total",
                        attributes={
                            "result": "error",
                            **activation_attributes,
                        },
                    )
                    activated = False

                if activated:
                    activated_components.append(component)
                    active_symbols.add(symbol)
                    seen_symbols.add(symbol)
                    logger.info(
                        "Runtime scanner activated",
                        log_type="runtime.activation.succeeded",
                        instrument=component.instrument_name,
                        **activation_attributes,
                    )
                    metrics.increment(
                        "trader_runtime_activations_total",
                        attributes={
                            "result": "success",
                            **activation_attributes,
                        },
                    )

    return activated_components


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
                    "Warmup skipped because strategy does not support warmup",
                    log_type="warmup.skipped",
                    strategy=component.strategy_name,
                    instrument=component.instrument_name,
                )
                metrics.increment(
                    "trader_warmups_total",
                    attributes={
                        "result": "skipped",
                        "strategy": component.strategy_name,
                        "instrument": component.instrument_name,
                    },
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
                "Warmup replayed bars",
                log_type="warmup.completed",
                strategy=component.strategy_name,
                instrument=component.instrument_name,
                bars=len(bars),
                ready=ready,
            )
            metrics.increment(
                "trader_warmups_total",
                attributes={
                    "result": "success",
                    "strategy": component.strategy_name,
                    "instrument": component.instrument_name,
                },
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
                "Warmup failed",
                log_type="warmup.failed",
                strategy=component.strategy_name,
                instrument=component.instrument_name,
                error=str(exc),
            )
            metrics.increment(
                "trader_warmups_total",
                attributes={
                    "result": "error",
                    "strategy": component.strategy_name,
                    "instrument": component.instrument_name,
                },
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
        
    run_id = getattr(settings, "run_id", None) or uuid4().hex
    recovery_service = PositionRecoveryService(_db, run_id=run_id)

    is_paper = os.environ.get("TRADING_MODE", "paper").lower() == "paper"
    api_key = os.environ.get("EXCHANGE_API_KEY", "")
    api_secret = os.environ.get("EXCHANGE_SECRET_KEY", "")

    exchange.apiKey = api_key
    exchange.secret = api_secret
    if is_paper and hasattr(exchange, "set_sandbox_mode"):
        exchange.set_sandbox_mode(True)

    try:
        raw_positions = exchange.fetch_positions()
        exchange_positions = []
        for p in raw_positions:
            if float(p.get("contracts", 0)) > 0:
                side = p.get("side", "long").upper()
                exchange_positions.append(ExchangePositionSnapshot(
                    instrument_id=p.get("symbol", ""),
                    symbol=p.get("symbol", ""),
                    side=side,
                    quantity=float(p.get("contracts", 0)),
                    avg_entry_price=float(p.get("entryPrice", 0)),
                    unrealized_pnl=float(p.get("unrealizedPnl", 0) or 0)
                ))
    except Exception as exc:
        logger.error(
            "Failed to fetch exchange positions for recovery",
            log_type="recovery.exchange_fetch_failed",
            error=str(exc)
        )
        exchange_positions = []

    components = (
        initial_components
        if instrument_name is not None or strategy_name is not None
        else build_startup_live_components(
            settings, 
            scanner=_scanner, 
            db=_db,
            exchange_positions=exchange_positions,
        )
    )

    venue_str = primary_component.venue.value.upper()
    data_clients, exec_clients = build_live_client_configs(
        components,
        is_paper=is_paper,
        api_key=api_key,
        api_secret=api_secret,
    )

    db_state = [
        StrategyPositionState(**row)
        for row in _db.get_open_strategy_positions()
    ]
    
    decisions = recovery_service.reconcile(exchange_positions, db_state, trigger="startup")
    
    for component in components:
        if hasattr(component.strategy, "set_recovery_service"):
            component.strategy.set_recovery_service(recovery_service)
            
        ccxt_symbol = component.instrument_spec.ccxt_symbol
        decision = next((d for d in decisions if d.symbol == ccxt_symbol), None)
        
        if decision:
            if decision.action == "block":
                if hasattr(component.strategy, "block_symbol"):
                    component.strategy.block_symbol(ccxt_symbol, decision.reason)
            elif decision.action == "recover" and decision.db_state is not None:
                if hasattr(component.strategy, "hydrate_position"):
                    component.strategy.hydrate_position(decision.db_state)
            elif decision.action == "adopt" and decision.exchange_snapshot is not None:
                if hasattr(component.strategy, "schedule_adoption"):
                    component.strategy.schedule_adoption(decision.exchange_snapshot)
        
        if ccxt_symbol in recovery_service.blocked_symbols:
            if hasattr(component.strategy, "block_symbol"):
                component.strategy.block_symbol(ccxt_symbol, "startup_reconciliation_blocked")

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

    global _runtime_node, _runtime_settings, _runtime_symbols, _runtime_strategies
    _runtime_node = node
    _runtime_settings = settings
    _runtime_symbols = component_ccxt_symbols(components)
    _runtime_strategies = {}
    for component in components:
        if callable(getattr(component.strategy, "activate_component", None)):
            _runtime_strategies.setdefault(component.strategy_name, component.strategy)

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
