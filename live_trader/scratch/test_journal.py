"""
Verification script for the Discovery Journal (L1 Feedback Loop).
"""

from src.core.broker_factory import BrokerFactory
from src.core.models import TradingMode
from src.intelligence.journal import DiscoveryJournal
from src.core.database import DatabaseManager
from src.main import load_config
import os


def test_journal_loop():
    config = load_config()
    broker = BrokerFactory.create(config, TradingMode.PAPER)
    
    # Use a test DB
    db_path = "data/test_journal.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        
    db = DatabaseManager(db_path=db_path)
    journal = DiscoveryJournal(broker=broker, db=db)

    print("--- 1. Recording Mock discovery ---")
    mock_data = {
        "symbol": "BTC/USDT:USDT",
        "last_price": 65000.0,
        "volume_24h": 50000000.0,
        "score": 15.5,
        "regime": "TRENDING_LOW_VOL"
    }
    journal.record(mock_data)

    print("\n--- 2. Verifying Journal Persistence ---")
    pending = db.get_pending_discoveries()
    if pending and pending[0]["symbol"] == "BTC/USDT:USDT":
        print(f"PASS: Discovery recorded in DB. ID: {pending[0]['id']}")
    else:
        print("FAIL: Discovery not found in DB.")

    print("\n--- 3. Testing Performance Update (Manual) ---")
    # This will check the current price and update the journal
    journal.update_performance()
    
    updated = db.get_pending_discoveries()[0]
    print(f"Current Status check finished. Max Price recorded: {updated.get('max_price')}")

    print("\n--- 4. Generating Report ---")
    print(journal.generate_short_report())


if __name__ == "__main__":
    test_journal_loop()
