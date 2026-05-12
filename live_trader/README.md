# Live Trader (Legacy CCXT Bot)

This directory contains the full original live trading bot that was deleted during the NautilusTrader migration (commit `d9e378a` — May 4, 2026).

**It was a fully functional live trading system** with:
- Exchange connectivity via CCXT (Bybit, Binance)
- Dynamic market scanner (`intelligence/scanner.py`) — finds momentum bursts across 200+ perpetuals
- Discovery Journal — logs every signal and audits outcomes at 1h/4h/24h
- Kill switch — halts trading if daily loss exceeds threshold
- Risk manager — position sizing, max exposure
- Regime detector — classifies trending vs. choppy markets
- Scheduler — runs scans, journal reviews, position checks on cron
- Full backtest engine (separate from NautilusTrader)
- Strategies: vwap_reversion, supertrend, donchian_breakout, ema_crossover, etc.

## Structure

```
live_trader/
├── main.py               ← entry point (uv run start)
├── core/
│   ├── broker.py         ← CCXT exchange wrapper (Bybit/Binance)
│   ├── scheduler.py      ← event loop (discovery_scan, position_check, etc.)
│   ├── database.py       ← SQLite persistence
│   ├── data_feed.py      ← OHLCV fetching
│   └── ...
├── intelligence/
│   ├── scanner.py        ← MarketScanner V2 — finds HOT MOMENTUM
│   ├── journal.py        ← DiscoveryJournal — logs & audits signals
│   └── regime_detector.py
├── risk/
│   ├── kill_switch.py    ← daily loss limiter
│   ├── manager.py        ← position sizing
│   └── position_sizer.py
├── strategies/           ← CCXT-based strategy implementations
├── notifications/        ← Telegram/Slack alerts
├── regimes.yaml          ← regime filter config
├── strategies.yaml       ← strategy parameters
└── watchlist.yaml        ← default instrument watchlist
```

## Why it exists here

Before comparing what to migrate to NautilusTrader vs. what to keep, we need the original code visible to make informed decisions. Key questions:
- The scanner, journal, kill switch, and risk manager are framework-agnostic — they can run alongside NautilusTrader
- The strategy implementations are CCXT-native but the logic can be ported to NautilusTrader Strategy classes
- The backtest engine here is now superseded by NautilusTrader

## Current server status
The Ubuntu server (`pm2 show trader-ai`) is still running this old code frozen at the pre-migration commit. It discovers signals but the scanner thresholds are too tight for the current low-volatility market.
