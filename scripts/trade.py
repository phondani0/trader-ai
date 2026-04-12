#!/usr/bin/env python
"""
Manual Test Trading CLI
========================

Usage:
    uv run trade --symbol BTC/USDT:USDT --side buy --qty 0.01
    uv run trade --symbol BTC/USDT:USDT --side buy --qty 0.01 --round-trip
    uv run trade --close-all
"""

import argparse
import sys
import time

import yaml
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

from src.core.broker_factory import BrokerFactory
from src.core.models import TradingMode, Order, OrderType, Side


PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def _load_config() -> dict:
    settings = {}
    for f in ["settings.yaml", "strategies.yaml", "watchlist.yaml"]:
        path = CONFIG_DIR / f
        if path.exists():
            with open(path) as fh:
                settings.update(yaml.safe_load(fh) or {})
    return settings


def main():
    parser = argparse.ArgumentParser(description="Trader AI — Manual Trade Injector")
    parser.add_argument(
        "--symbol",
        help="Symbol to trade (e.g. BTC/USDT:USDT for CCXT or SPY for Alpaca)",
    )
    parser.add_argument("--side", choices=["buy", "sell"], help="Trade side (buy/sell)")
    parser.add_argument("--qty", type=float, help="Quantity to trade")
    parser.add_argument("--type", default="market", choices=["market", "limit"], help="Order type")
    parser.add_argument("--limit-price", type=float, default=None, help="Limit price if type is limit")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"], help="Trading mode")
    parser.add_argument("--close-all", action="store_true", help="Force wipe all open positions and orders")
    parser.add_argument("--round-trip", action="store_true", help="Instantly buy and sell back to test full lifecycle")

    args = parser.parse_args()
    load_dotenv()

    mode = TradingMode.LIVE if args.mode == "live" else TradingMode.PAPER
    if mode == TradingMode.LIVE:
        confirm = input("\n⚠️  You are about to execute a LIVE trade. Type 'CONFIRM' to proceed: ")
        if confirm != "CONFIRM":
            print("Aborted.")
            sys.exit(0)

    config = _load_config()
    broker = BrokerFactory.create(config, mode)

    print("==================================================")
    print(f"🤖 Manual Trade Override — {type(broker).__name__}")
    print("==================================================")

    if args.close_all:
        print("🚨 EMERGENCY OVERRIDE: Closing all positions and cancelling orders...")
        broker.cancel_all_orders()
        broker.close_all_positions()
        print("✅ All positions squared off successfully.")
        sys.exit(0)

    # Basic validations
    if not (args.symbol and args.side and args.qty):
        print("❌ ERROR: --symbol, --side, and --qty are required to place a trade.")
        sys.exit(1)

    # Short-sell capability check (broker decides, not us)
    if args.side == "sell" and not broker.supports_short_selling:
        pos = broker.get_position(args.symbol.replace("/", ""))
        if not pos or pos.quantity < args.qty:
            print(f"❌ ERROR: This broker does not support short selling.")
            print(f"   You requested to sell {args.qty} {args.symbol}, "
                  f"but you have {pos.quantity if pos else 0.0} available.")
            sys.exit(1)

    print(f"Current Buying Power: ${broker.get_buying_power():,.2f}")

    order = Order(
        symbol=args.symbol,
        side=Side.BUY if args.side == "buy" else Side.SELL,
        quantity=args.qty,
        order_type=OrderType.MARKET if args.type == "market" else OrderType.LIMIT,
        limit_price=args.limit_price,
        strategy_name="manual_override",
    )

    print(f"\nTransmitting Order...")
    try:
        res = broker.place_order(order)
        print(f"✅ SUCCESS: {args.side.upper()} {args.qty}x {args.symbol}")
        print(f"   Order ID: {res.id}")
        print(f"   Status:   {res.status.value.upper()}")
        print(f"   Filled:   {res.filled_quantity or 0.0} @ ${res.filled_price or 0.0}")
    except Exception as e:
        print(f"❌ ORDER FAILED: {str(e)}")
        sys.exit(1)

    if args.round_trip and args.side == "buy":
        print("\n⏳ Round-Trip Mode: Waiting 3 seconds for buy to fill...")
        time.sleep(3)
        print(f"\nTransmitting Reverse (SELL) Order...")
        sell_order = Order(
            symbol=args.symbol, side=Side.SELL, quantity=args.qty,
            order_type=OrderType.MARKET, strategy_name="manual_roundtrip",
        )
        try:
            res_sell = broker.place_order(sell_order)
            print(f"✅ ROUND-TRIP SUCCESS: SELL {args.qty}x {args.symbol}")
            print(f"   Order ID: {res_sell.id}")
            print(f"   Status:   {res_sell.status.value.upper()}")
        except Exception as e:
            print(f"❌ ROUND-TRIP SELL FAILED: {str(e)}")

    print("\nFetching Live Positions...")
    time.sleep(2)

    positions = broker.get_positions()
    if not positions:
        print("   No active positions.")
    else:
        for p in positions:
            print(f"   {p.symbol}: {p.quantity} units (Entry: ${p.entry_price:.2f})")

    print("==================================================")


if __name__ == "__main__":
    main()
