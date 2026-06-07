from nautilus_trader.config import TradingNodeConfig, LiveDataEngineConfig, LiveExecEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.adapters.binance.config import BinanceDataClientConfig, BinanceExecClientConfig
from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory, BinanceLiveExecClientFactory

def test():
    data_config = BinanceDataClientConfig()
    exec_config = BinanceExecClientConfig()
    node_config = TradingNodeConfig(
        data_engine=LiveDataEngineConfig(),
        exec_engine=LiveExecEngineConfig(reconciliation=True),
        data_clients={"BINANCE": data_config},
        exec_clients={"BINANCE": exec_config}
    )
    node = TradingNode(config=node_config)
    node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
    node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)
    node.build()
    print("Node built successfully!")

test()
