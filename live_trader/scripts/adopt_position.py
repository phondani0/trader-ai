#!/usr/bin/env python

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.core.models import TradingMode
from src.main import TradingEngine, load_config, setup_logging


def main():
    parser = argparse.ArgumentParser(
        description="Trader AI — Adopt an existing exchange position into managed state"
    )
    parser.add_argument("--symbol", required=True, help="Position symbol, e.g. BTC/USDT:USDT")
    parser.add_argument("--strategy", required=True, help="Strategy name to attribute to the adopted position")
    parser.add_argument("--stop-loss", required=True, type=float, help="Stop-loss price to manage on the adopted position")
    parser.add_argument("--take-profit", required=True, type=float, help="Take-profit price to manage on the adopted position")
    parser.add_argument("--mode", default="live", choices=["paper", "live"], help="Trading mode")
    parser.add_argument("--reason", default="Manual adoption of legacy exchange position", help="Reason stored in the adopted signal")

    args = parser.parse_args()
    load_dotenv()

    mode = TradingMode.LIVE if args.mode == "live" else TradingMode.PAPER
    if mode == TradingMode.LIVE:
        confirm = input(
            "\n⚠️  You are about to adopt and update protection on a LIVE exchange position. "
            "Type 'ADOPT' to proceed: "
        )
        if confirm != "ADOPT":
            print("Aborted.")
            sys.exit(0)

    config = load_config()
    setup_logging(config)
    engine = TradingEngine(config=config, mode=mode)

    managed = engine.adopt_exchange_position(
        symbol=args.symbol,
        strategy_name=args.strategy,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        reason=args.reason,
    )

    signal = managed["signal"]
    print("==================================================")
    print("✅ Position adopted into managed runtime state")
    print(f"Symbol:      {args.symbol}")
    print(f"Strategy:    {signal.strategy_name}")
    print(f"Side:        {managed['side'].value}")
    print(f"Quantity:    {managed['quantity']}")
    print(f"Entry Price: {managed['entry_price']}")
    print(f"Stop Loss:   {signal.stop_loss}")
    print(f"Take Profit: {signal.take_profit}")
    print("==================================================")


if __name__ == "__main__":
    main()
