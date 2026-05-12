"""
Legacy entrypoint kept for backward compatibility.
Forwards to the new NautilusTrader live CLI.
"""
from trader_ai.cli.live import main


if __name__ == "__main__":
    main()
