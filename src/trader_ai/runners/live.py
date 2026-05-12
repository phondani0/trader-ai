from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from pathlib import Path

from nautilus_trader.adapters.binance.config import (
    BinanceDataClientConfig,
    BinanceExecClientConfig,
)
from nautilus_trader.adapters.binance.factories import (
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.config import (
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode

import ccxt
from trader_ai.config import load_settings
from trader_ai.runners.backtest import build_backtest_components

from live_trader.core.database import DatabaseManager
from live_trader.intelligence.journal import DiscoveryJournal
from live_trader.intelligence.scanner import MarketScanner

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Global state for sidecars
_db: DatabaseManager | None = None
_journal: DiscoveryJournal | None = None
_scanner: MarketScanner | None = None


def _trigger_discovery_scan():
    """Sidecar task: Run MarketScanner V2."""
    from loguru import logger
    if _scanner:
        logger.info("Running scheduled MarketScanner discovery...")
        _scanner.scan_for_momentum()


def _trigger_journal_review():
    """Sidecar task: Run DiscoveryJournal audit."""
    from loguru import logger
    if _journal:
        logger.info("Running scheduled DiscoveryJournal review...")
        _journal.update_performance()

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
    
    # Initialize the Sidecars
    global _db, _journal, _scanner
    if _db is None:
        _db = DatabaseManager()
        exchange = ccxt.binanceusdm()
        _journal = DiscoveryJournal(exchange, _db, settings.model_dump() if hasattr(settings, "model_dump") else settings)
        _scanner = MarketScanner(exchange, settings.model_dump() if hasattr(settings, "model_dump") else settings, _journal)

    # Reuse the same strategy/instrument builder as backtesting
    components = build_backtest_components(
        settings,
        instrument_name=instrument_name,
        strategy_name=strategy_name,
        is_live=True,
    )
    
    # 1. Configure the TradingNode
    node_config = TradingNodeConfig(
        data_engine=LiveDataEngineConfig(
            time_source="system",
            msgbus_publisher_port=0,
            msgbus_reqrep_port=0,
        ),
        exec_engine=LiveExecEngineConfig(
            reconcile_balances=True,
            reconcile_positions=True,
            reconcile_orders=True,
        ),
        risk_engine=LiveRiskEngineConfig(),
    )
    node = TradingNode(config=node_config)
    
    is_paper = os.environ.get("TRADING_MODE", "paper").lower() == "paper"
    api_key = os.environ.get("EXCHANGE_API_KEY", "")
    api_secret = os.environ.get("EXCHANGE_SECRET_KEY", "")
    
    # Use testnet URL if in paper mode
    base_uri = "wss://stream.binancefuture.com" if is_paper else "wss://fstream.binance.com"
    api_url = "https://testnet.binancefuture.com" if is_paper else "https://fapi.binance.com"
    
    # 2. Add the Binance Data Client
    data_client_config = BinanceDataClientConfig(
        base_uri=base_uri,
        instrument_provider=components.instrument_provider,
        api_key=api_key,
        api_secret=api_secret,
    )
    
    data_client = BinanceLiveDataClientFactory.create(
        config=data_client_config,
        msgbus=node.msgbus,
        cache=node.cache,
        clock=node.clock,
        loop=asyncio.get_event_loop(),
    )
    node.add_data_client(data_client)
    
    # 3. Add the Binance Execution Client
    exec_client_config = BinanceExecClientConfig(
        base_uri=api_url,
        api_key=api_key,
        api_secret=api_secret,
        instrument_provider=components.instrument_provider,
    )
    
    exec_client = BinanceLiveExecClientFactory.create(
        config=exec_client_config,
        msgbus=node.msgbus,
        cache=node.cache,
        clock=node.clock,
        loop=asyncio.get_event_loop(),
    )
    node.add_exec_client(exec_client)
    
    # 4. Add the Strategy
    node.trader.add_strategy(components.strategy)
    
    # 5. Wire the sidecar tasks (Scanner and Journal) to the Node's Clock
    discovery_interval = int(settings.discovery.get("interval_minutes", 10))
    node.clock.set_alert(
        name="discovery_scan",
        interval=timedelta(minutes=discovery_interval),
        callback=_trigger_discovery_scan,
    )
    
    node.clock.set_alert(
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
