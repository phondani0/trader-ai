from __future__ import annotations

from nautilus_app.runners.backtest import run_backtest


def main():
    result = run_backtest()
    print("NautilusTrader backtest completed")
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
