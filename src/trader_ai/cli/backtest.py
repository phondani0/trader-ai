from __future__ import annotations

import argparse

from trader_ai.runners.backtest import run_backtest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a NautilusTrader backtest")
    parser.add_argument("--settings", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--data-source", default=None)
    parser.add_argument("--bars-csv", default=None)
    parser.add_argument("--instrument", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--fast-ema-period", type=int, default=None)
    parser.add_argument("--slow-ema-period", type=int, default=None)
    parser.add_argument("--ccxt-limit", type=int, default=None)
    parser.add_argument("--starting-balance", default=None)
    parser.add_argument(
        "--use-strategy-config",
        action="store_true",
        help="Load strategy params from config/strategies.yaml for production-faithful research",
    )
    return parser


def main():
    args = build_parser().parse_args()
    result = run_backtest(
        settings_path=args.settings,
        output_dir=args.output_dir,
        strategy_name=args.strategy,
        data_source=args.data_source,
        bars_csv=args.bars_csv,
        instrument_name=args.instrument,
        cache_dir=args.cache_dir,
        fast_ema_period=args.fast_ema_period,
        slow_ema_period=args.slow_ema_period,
        ccxt_limit=args.ccxt_limit,
        use_strategy_config=args.use_strategy_config,
        starting_balance=args.starting_balance,
    )
    print("NautilusTrader backtest completed")
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
