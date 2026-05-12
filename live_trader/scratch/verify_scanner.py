"""
Verification script for the restored Discovery Scanner.
"""

from src.core.broker_factory import BrokerFactory
from src.core.models import TradingMode
from src.intelligence.scanner import MarketScanner
from src.main import load_config


def test_scanner():
    config = load_config()
    broker = BrokerFactory.create(config, TradingMode.PAPER)

    scanner = MarketScanner(broker=broker, config=config)
    print("Testing restored scanner manuals...")
    movers = scanner.scan_for_momentum()

    if movers:
        print(f"PASS: Found {len(movers)} momentum opportunities.")
    else:
        print("INFO: No movers found (market might be flat).")


if __name__ == "__main__":
    test_scanner()
