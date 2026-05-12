"""
Broker Factory.

Reads config and returns the correct BaseBroker implementation.
This is the ONLY place in the codebase that knows about concrete broker classes.

Usage:
    broker = BrokerFactory.create(config, mode)
    # broker is typed as BaseBroker — consumers never see the concrete class.
"""

from __future__ import annotations


from src.core.base_broker import BaseBroker
from src.core.models import TradingMode


class BrokerFactory:
    """
    Factory that creates the correct broker based on config.

    To add a new broker:
        1. Create src/core/my_broker.py inheriting BaseBroker
        2. Add an elif branch here
        3. Done — zero changes anywhere else in the codebase.
    """

    @staticmethod
    def create(config: dict, mode: TradingMode = TradingMode.PAPER) -> BaseBroker:
        """
        Create a broker instance based on configuration.

        Args:
            config: Full application config (from settings.yaml)
            mode: Paper or live trading

        Returns:
            A BaseBroker instance (CCXTBroker, AlpacaBroker, etc.)
        """
        broker_name = config.get("broker", {}).get("name", "ccxt").lower()

        if broker_name == "ccxt":
            from src.core.broker import CCXTBroker
            return CCXTBroker(mode=mode, config=config)

        elif broker_name == "alpaca":
            from src.core.alpaca_broker import AlpacaBroker
            return AlpacaBroker(mode=mode, config=config)

        else:
            raise ValueError(
                f"Unknown broker: '{broker_name}'. "
                f"Supported: 'ccxt', 'alpaca'. "
                f"Check broker.name in config/settings.yaml"
            )
