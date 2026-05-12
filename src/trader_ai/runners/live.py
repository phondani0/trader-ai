from __future__ import annotations

import asyncio
from pathlib import Path

from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory
from nautilus_trader.config import LiveDataEngineConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode

from trader_ai.config import load_settings
from trader_ai.runners.backtest import build_backtest_components

PROJECT_ROOT = Path(__file__).resolve().parents[3]

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
    components = build_backtest_components(
        settings,
        instrument_name=instrument_name,
        strategy_name=strategy_name,
    )
    
    # 1. Configure the TradingNode
    node_config = TradingNodeConfig(
        data_engine=LiveDataEngineConfig(
            time_source="system",
            msgbus_publisher_port=0,
            msgbus_reqrep_port=0,
        ),
    )
    node = TradingNode(config=node_config)
    
    # 2. Add the Binance Data Client
    data_client_config = BinanceDataClientConfig(
        base_uri="wss://fstream.binance.com",  # USDT-M Futures
        instrument_provider=components.instrument_provider,
        api_key=None,     # Not needed for public data
        api_secret=None,  # Not needed for public data
    )
    
    data_client = BinanceLiveDataClientFactory.create(
        config=data_client_config,
        msgbus=node.msgbus,
        cache=node.cache,
        clock=node.clock,
        loop=asyncio.get_event_loop(),
    )
    node.add_data_client(data_client)
    
    # 3. Add the Strategy
    node.trader.add_strategy(components.strategy)
    
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
    
    # Block forever while asyncio loop handles WebSocket events
    node.run()
