"""
Market Intelligence — discovery scanner.

Scanner V2 builds a dynamic liquid universe from the full exchange, then
enriches the strongest names with short-term momentum and breakout features.
The goal is to find real burst candidates quickly instead of relying on a
static watchlist.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd
from loguru import logger


import ccxt

from src.intelligence.journal import DiscoveryJournal

_TIMEFRAME_MAP = {
    "1Min": "1m",
    "5Min": "5m",
    "15Min": "15m",
    "30Min": "30m",
    "1Hour": "1h",
    "4Hour": "4h",
    "1Day": "1d",
}


class MarketScanner:
    """
    Exchange-wide discovery scanner for liquid momentum and breakout bursts.
    """

    def __init__(
        self,
        exchange: ccxt.Exchange,
        config: dict,
        journal: DiscoveryJournal | None = None,
    ):
        self.exchange = exchange
        self.config = config
        self.journal = journal

        scan_cfg = config.get("discovery", {})
        data_cfg = config.get("data", {})

        self.enabled = bool(scan_cfg.get("enabled", True))
        self.min_24h_volume = float(scan_cfg.get("min_24h_volume", 10_000_000))
        self.min_price_change = float(scan_cfg.get("min_price_change", 3.0))
        self.min_trade_score = float(
            scan_cfg.get("min_trade_score", self.min_price_change)
        )
        self.top_n = int(scan_cfg.get("top_n", 5))
        self.candidate_ttl_minutes = int(scan_cfg.get("candidate_ttl_minutes", 30))

        self.dynamic_monitor_size = int(scan_cfg.get("dynamic_monitor_size", 10))
        self.liquid_universe_size = int(scan_cfg.get("liquid_universe_size", 30))
        feature_timeframe = scan_cfg.get(
            "feature_timeframe", data_cfg.get("candle_interval", "5Min")
        )
        self.feature_timeframe = _TIMEFRAME_MAP.get(feature_timeframe, feature_timeframe)
        self.feature_window_bars = int(scan_cfg.get("feature_window_bars", 96))
        self.breakout_lookback_bars = int(scan_cfg.get("breakout_lookback_bars", 20))
        self.momentum_lookback_bars = int(scan_cfg.get("momentum_lookback_bars", 12))
        self.acceleration_lookback_bars = int(
            scan_cfg.get("acceleration_lookback_bars", 3)
        )
        self.relative_volume_lookback_bars = int(
            scan_cfg.get("relative_volume_lookback_bars", 20)
        )
        self.min_relative_volume = float(scan_cfg.get("min_relative_volume", 1.15))
        self.min_intraday_momentum_pct = float(
            scan_cfg.get("min_intraday_momentum_pct", 1.0)
        )
        self.min_breakout_score = float(scan_cfg.get("min_breakout_score", 1.0))
        self.min_last_price = float(scan_cfg.get("min_last_price", 0.0))
        self.max_spread_bps = float(scan_cfg.get("max_spread_bps", 35.0))

        weight_cfg = scan_cfg.get("score_weights", {})
        self.score_weights = {
            "liquidity": float(weight_cfg.get("liquidity", 0.2)),
            "change_24h": float(weight_cfg.get("change_24h", 0.15)),
            "momentum_1h": float(weight_cfg.get("momentum_1h", 0.25)),
            "acceleration_15m": float(weight_cfg.get("acceleration_15m", 0.2)),
            "breakout": float(weight_cfg.get("breakout", 0.15)),
            "relative_volume": float(weight_cfg.get("relative_volume", 0.05)),
        }

        self._latest_candidates: dict[str, Dict] = {}
        self._latest_liquid_universe: list[Dict] = []
        self._last_scan_at: datetime | None = None

    def scan_for_momentum(self) -> List[Dict]:
        """
        One-shot scan of the exchange to find liquid burst candidates.
        """
        if not self.enabled:
            return []

        logger.info("🔍 Starting market scan for dynamic momentum and breakouts...")

        try:
            tickers = self.exchange.fetch_tickers()
            if not tickers:
                return []

            liquid_universe = self._build_liquid_universe(tickers)
            self._latest_liquid_universe = liquid_universe[: self.liquid_universe_size]

            enriched_candidates = self._enrich_candidates(self._latest_liquid_universe)
            movers = [
                candidate
                for candidate in enriched_candidates
                if self._is_trade_candidate(candidate)
            ]
            movers.sort(key=lambda row: row["score"], reverse=True)

            subset = movers[: self.top_n]
            self._last_scan_at = datetime.utcnow()
            self._refresh_candidate_cache(subset)

            if subset:
                if self.journal:
                    for mover in subset:
                        self.journal.record(mover)
                self._log_discovery(subset)
            else:
                logger.info("ℹ️ No high-quality dynamic momentum candidates found.")

            return subset

        except Exception as exc:
            logger.error(f"Discovery scanner error: {exc}")
            return []

    def build_tradable_universe(
        self, core_watchlist: list[str], persistent_symbols: list[str] | None = None
    ) -> list[str]:
        """Merge watchlist, managed symbols, dynamic universe, and active discoveries."""
        persistent = persistent_symbols or []
        dynamic_symbols = self.get_dynamic_universe_symbols()
        candidate_symbols = [
            candidate["symbol"] for candidate in self.get_active_candidates()
        ]

        ordered_symbols: list[str] = []
        for symbol in [
            *core_watchlist,
            *persistent,
            *dynamic_symbols,
            *candidate_symbols,
        ]:
            if symbol not in ordered_symbols:
                ordered_symbols.append(symbol)

        max_symbols = int(self.config.get("watchlist", {}).get("max_stocks", 20))
        required_count = len(list(dict.fromkeys([*core_watchlist, *persistent])))
        limit = max(max_symbols, required_count)
        return ordered_symbols[:limit]

    def get_active_candidates(self) -> list[Dict]:
        """Return recent discovery candidates that still qualify for trading."""
        now = datetime.utcnow()
        ttl = timedelta(minutes=self.candidate_ttl_minutes)
        active_candidates = [
            candidate
            for candidate in self._latest_candidates.values()
            if now - candidate["detected_at"] <= ttl
            and candidate["score"] >= self.min_trade_score
        ]
        return sorted(
            active_candidates,
            key=lambda candidate: candidate["score"],
            reverse=True,
        )

    def get_dynamic_universe_symbols(self) -> list[str]:
        """Return a rotating set of liquid symbols to monitor beyond the watchlist."""
        return [
            row["symbol"]
            for row in self._latest_liquid_universe[: self.dynamic_monitor_size]
        ]

    def get_candidate_context(self, symbol: str) -> Dict | None:
        """Get metadata for an active discovery candidate."""
        for candidate in self.get_active_candidates():
            if candidate["symbol"] == symbol:
                return candidate
        return None

    def _build_liquid_universe(self, tickers: dict[str, dict]) -> list[Dict]:
        ranked: list[Dict] = []

        for symbol, ticker in tickers.items():
            if not self._is_supported_symbol(symbol):
                continue

            last_price = float(ticker.get("last", 0.0) or 0.0)
            volume_24h = float(ticker.get("quoteVolume", 0.0) or 0.0)
            change_24h = float(ticker.get("change", 0.0) or 0.0)
            bid = float(ticker.get("bid", 0.0) or 0.0)
            ask = float(ticker.get("ask", 0.0) or 0.0)
            spread_bps = self._spread_bps(bid, ask, last_price)

            if last_price <= self.min_last_price:
                continue
            if volume_24h < self.min_24h_volume:
                continue
            if spread_bps > self.max_spread_bps:
                continue

            open_interest_value = float(ticker.get("openInterestValue", 0.0) or 0.0)
            liquidity_score = self._score_liquidity(
                volume_24h,
                spread_bps,
                open_interest_value,
            )
            universe_score = liquidity_score + min(
                2.5,
                abs(change_24h) / max(self.min_price_change, 1.0),
            )

            ranked.append(
                {
                    "symbol": symbol,
                    "change": change_24h,
                    "volume_24h": volume_24h,
                    "last_price": last_price,
                    "bid": bid,
                    "ask": ask,
                    "spread_bps": spread_bps,
                    "liquidity_score": round(liquidity_score, 4),
                    "universe_score": round(universe_score, 4),
                    "open_interest_value": open_interest_value,
                    "funding_rate": float(ticker.get("fundingRate", 0.0) or 0.0),
                    "prev_price_1h": float(ticker.get("prevPrice1h", 0.0) or 0.0),
                }
            )

        ranked.sort(key=lambda row: row["universe_score"], reverse=True)
        return ranked

    def _enrich_candidates(self, candidates: list[Dict]) -> list[Dict]:
        enriched: list[Dict] = []
        min_bars = max(
            self.breakout_lookback_bars + 2,
            self.momentum_lookback_bars + 2,
            self.relative_volume_lookback_bars + 2,
            self.acceleration_lookback_bars + 2,
        )

        for candidate in candidates:
            enriched_candidate = dict(candidate)
            intraday = self._load_intraday_features(candidate["symbol"], min_bars)
            if intraday:
                enriched_candidate.update(intraday)
            else:
                enriched_candidate.update(
                    {
                        "direction": "long"
                        if candidate["change"] >= 0
                        else "short",
                        "scanner_profile": "rotation",
                        "burst_score": 0.0,
                        "breakout_score": 0.0,
                        "relative_volume": 1.0,
                        "return_15m_pct": 0.0,
                        "return_1h_pct": 0.0,
                        "return_4h_pct": 0.0,
                        "breakout_pct": 0.0,
                    }
                )

            enriched_candidate["discovery_source"] = "scanner_v2"
            enriched_candidate["score"] = round(
                self._combine_score(enriched_candidate),
                4,
            )
            enriched_candidate["metadata"] = self._build_metadata(enriched_candidate)
            enriched.append(enriched_candidate)

        enriched.sort(key=lambda row: row["score"], reverse=True)
        return enriched

    def _load_intraday_features(
        self,
        symbol: str,
        min_bars: int,
    ) -> Dict | None:
        try:
            ohlcv = self.exchange.fetch_ohlcv(
                symbol,
                timeframe=self.feature_timeframe,
                limit=max(self.feature_window_bars, min_bars),
            )
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as exc:
            logger.debug(f"Failed to fetch intraday candles for {symbol}: {exc}")
            return None

        if df.empty or len(df) < min_bars:
            return None

        return self._compute_intraday_features(df)

    def _compute_intraday_features(self, df: pd.DataFrame) -> Dict:
        closes = df["close"].astype(float)
        highs = df["high"].astype(float)
        lows = df["low"].astype(float)
        volumes = df["volume"].astype(float)

        current_close = float(closes.iloc[-1])
        current_volume = float(volumes.iloc[-1])

        ret_15m = self._window_return_pct(closes, self.acceleration_lookback_bars)
        ret_1h = self._window_return_pct(closes, self.momentum_lookback_bars)
        ret_4h = self._window_return_pct(
            closes,
            min(len(closes) - 1, self.momentum_lookback_bars * 4),
        )
        acceleration_pct = ret_15m - (ret_1h / max(self.momentum_lookback_bars / max(self.acceleration_lookback_bars, 1), 1.0))

        rolling_high = highs.shift(1).rolling(
            self.breakout_lookback_bars,
            min_periods=max(5, self.breakout_lookback_bars // 2),
        ).max()
        rolling_low = lows.shift(1).rolling(
            self.breakout_lookback_bars,
            min_periods=max(5, self.breakout_lookback_bars // 2),
        ).min()
        recent_high = float(rolling_high.iloc[-1]) if not pd.isna(rolling_high.iloc[-1]) else current_close
        recent_low = float(rolling_low.iloc[-1]) if not pd.isna(rolling_low.iloc[-1]) else current_close

        breakout_up_pct = (
            ((current_close / recent_high) - 1) * 100 if recent_high > 0 else 0.0
        )
        breakout_down_pct = (
            ((recent_low / current_close) - 1) * 100
            if recent_low > 0 and current_close > 0
            else 0.0
        )

        prev_volume = volumes.iloc[-1 - self.relative_volume_lookback_bars : -1]
        avg_volume = float(prev_volume.mean()) if not prev_volume.empty else 0.0
        relative_volume = current_volume / avg_volume if avg_volume > 0 else 1.0

        recent_volume = volumes.tail(self.acceleration_lookback_bars)
        baseline_volume = volumes.iloc[
            -1 - self.relative_volume_lookback_bars : -1 - self.acceleration_lookback_bars
        ]
        volume_burst = (
            float(recent_volume.mean()) / float(baseline_volume.mean())
            if not baseline_volume.empty and float(baseline_volume.mean()) > 0
            else relative_volume
        )

        trend_efficiency_pct = self._trend_efficiency_pct(
            closes.tail(self.momentum_lookback_bars + 1)
        )

        long_burst = (
            max(ret_1h, 0.0)
            + max(ret_15m, 0.0) * 1.2
            + max(acceleration_pct, 0.0) * 0.8
            + max(relative_volume - 1.0, 0.0) * 2.0
            + max(volume_burst - 1.0, 0.0)
            + max(breakout_up_pct * 5.0, 0.0)
        )
        short_burst = (
            max(-ret_1h, 0.0)
            + max(-ret_15m, 0.0) * 1.2
            + max(-acceleration_pct, 0.0) * 0.8
            + max(relative_volume - 1.0, 0.0) * 2.0
            + max(volume_burst - 1.0, 0.0)
            + max(breakout_down_pct * 5.0, 0.0)
        )

        if long_burst >= short_burst:
            direction = "long"
            breakout_pct = breakout_up_pct
            burst_score = long_burst
        else:
            direction = "short"
            breakout_pct = breakout_down_pct
            burst_score = short_burst

        breakout_score = max(0.0, breakout_pct * 5.0) + max(
            0.0, trend_efficiency_pct / 25.0
        )

        scanner_profile = "breakout"
        if breakout_score < self.min_breakout_score and abs(ret_1h) >= self.min_intraday_momentum_pct:
            scanner_profile = "continuation"
        elif breakout_score < self.min_breakout_score:
            scanner_profile = "rotation"

        return {
            "direction": direction,
            "scanner_profile": scanner_profile,
            "burst_score": round(burst_score, 4),
            "breakout_score": round(breakout_score, 4),
            "relative_volume": round(relative_volume, 4),
            "volume_burst": round(volume_burst, 4),
            "return_15m_pct": round(ret_15m, 4),
            "return_1h_pct": round(ret_1h, 4),
            "return_4h_pct": round(ret_4h, 4),
            "acceleration_pct": round(acceleration_pct, 4),
            "breakout_pct": round(breakout_pct, 4),
            "trend_efficiency_pct": round(trend_efficiency_pct, 4),
        }

    def _combine_score(self, candidate: Dict) -> float:
        change_component = min(
            5.0,
            abs(float(candidate.get("change", 0.0) or 0.0))
            / max(self.min_price_change, 1.0),
        )
        momentum_component = min(
            5.0,
            abs(float(candidate.get("return_1h_pct", 0.0) or 0.0))
            / max(self.min_intraday_momentum_pct, 0.5),
        )
        acceleration_component = min(
            5.0,
            abs(float(candidate.get("acceleration_pct", 0.0) or 0.0))
            / max(self.min_intraday_momentum_pct, 0.5),
        )
        breakout_component = min(
            5.0,
            float(candidate.get("breakout_score", 0.0) or 0.0),
        )
        relative_volume_component = min(
            5.0,
            max(
                0.0,
                float(candidate.get("relative_volume", 1.0) or 1.0)
                - self.min_relative_volume,
            )
            * 4.0,
        )

        return (
            float(candidate.get("liquidity_score", 0.0) or 0.0)
            * self.score_weights["liquidity"]
            + change_component * self.score_weights["change_24h"]
            + momentum_component * self.score_weights["momentum_1h"]
            + acceleration_component * self.score_weights["acceleration_15m"]
            + breakout_component * self.score_weights["breakout"]
            + relative_volume_component * self.score_weights["relative_volume"]
        ) * 5.0

    def _build_metadata(self, candidate: Dict) -> Dict:
        return {
            "scanner_profile": candidate.get("scanner_profile"),
            "direction": candidate.get("direction"),
            "liquidity_score": candidate.get("liquidity_score"),
            "burst_score": candidate.get("burst_score"),
            "breakout_score": candidate.get("breakout_score"),
            "relative_volume": candidate.get("relative_volume"),
            "volume_burst": candidate.get("volume_burst"),
            "return_15m_pct": candidate.get("return_15m_pct"),
            "return_1h_pct": candidate.get("return_1h_pct"),
            "return_4h_pct": candidate.get("return_4h_pct"),
            "breakout_pct": candidate.get("breakout_pct"),
            "spread_bps": candidate.get("spread_bps"),
            "universe_score": candidate.get("universe_score"),
            "open_interest_value": candidate.get("open_interest_value"),
            "funding_rate": candidate.get("funding_rate"),
        }

    def _is_trade_candidate(self, candidate: Dict) -> bool:
        if float(candidate.get("score", 0.0) or 0.0) < self.min_trade_score:
            return False

        change_trigger = (
            abs(float(candidate.get("change", 0.0) or 0.0)) >= self.min_price_change
        )
        intraday_trigger = (
            abs(float(candidate.get("return_1h_pct", 0.0) or 0.0))
            >= self.min_intraday_momentum_pct
        )
        breakout_trigger = (
            float(candidate.get("breakout_score", 0.0) or 0.0) >= self.min_breakout_score
        )
        relative_volume = float(candidate.get("relative_volume", 1.0) or 1.0)

        if not (change_trigger or intraday_trigger or breakout_trigger):
            return False

        min_required_relative_volume = (
            self.min_relative_volume
            if breakout_trigger
            else max(0.75, self.min_relative_volume * 0.7)
        )
        if relative_volume < min_required_relative_volume:
            return False

        return True

    @staticmethod
    def _is_supported_symbol(symbol: str) -> bool:
        return symbol.endswith("/USDT:USDT") or symbol.endswith("/USDT")

    def _score_liquidity(
        self,
        volume_24h: float,
        spread_bps: float,
        open_interest_value: float = 0.0,
    ) -> float:
        volume_multiple = max(volume_24h / max(self.min_24h_volume, 1.0), 1.0)
        volume_score = min(5.0, 1.0 + math.log10(volume_multiple + 1.0) * 3.0)
        spread_score = max(
            0.0,
            1.5 * (1.0 - (spread_bps / max(self.max_spread_bps, 1.0))),
        )
        oi_score = 0.0
        if open_interest_value > 0:
            oi_score = min(1.5, max(0.0, math.log10(open_interest_value + 1.0) - 5.0))
        return volume_score + spread_score + oi_score

    @staticmethod
    def _spread_bps(bid: float, ask: float, last: float) -> float:
        if bid <= 0 or ask <= 0:
            return 0.0
        mid = (bid + ask) / 2 or last
        if mid <= 0:
            return 0.0
        return ((ask - bid) / mid) * 10_000

    @staticmethod
    def _window_return_pct(series: pd.Series, bars: int) -> float:
        bars = max(1, min(bars, len(series) - 1))
        if bars <= 0:
            return 0.0
        start = float(series.iloc[-1 - bars])
        end = float(series.iloc[-1])
        if start <= 0:
            return 0.0
        return ((end / start) - 1) * 100

    @staticmethod
    def _trend_efficiency_pct(series: pd.Series) -> float:
        if len(series) < 2:
            return 0.0
        path = series.diff().abs().sum()
        if not path or path <= 0:
            return 0.0
        return float(abs(series.iloc[-1] - series.iloc[0]) / path * 100)

    def _refresh_candidate_cache(self, movers: List[Dict]):
        now = datetime.utcnow()
        for mover in movers:
            self._latest_candidates[mover["symbol"]] = {
                **mover,
                "detected_at": now,
            }

        ttl = timedelta(minutes=self.candidate_ttl_minutes)
        self._latest_candidates = {
            symbol: candidate
            for symbol, candidate in self._latest_candidates.items()
            if now - candidate["detected_at"] <= ttl
        }

    def _log_discovery(self, movers: List[Dict]):
        """Visual terminal alert for the user."""
        print("\n" + "═" * 88)
        print("🔥  HOT MOMENTUM DISCOVERED")
        print("═" * 88)
        print(
            f"{'SYMBOL':<16} | {'DIR':<5} | {'PROFILE':<12} | {'1H %':<8} | "
            f"{'RVOL':<6} | {'SCORE':<7} | {'24H VOL'}"
        )
        print("-" * 88)

        for mover in movers:
            vol_str = f"${mover['volume_24h'] / 1_000_000:.1f}M"
            print(
                f"{mover['symbol']:<16} | "
                f"{str(mover.get('direction', '-')):<5} | "
                f"{str(mover.get('scanner_profile', '-')):<12} | "
                f"{mover.get('return_1h_pct', 0.0):<8.2f} | "
                f"{mover.get('relative_volume', 1.0):<6.2f} | "
                f"{mover['score']:<7.2f} | "
                f"{vol_str}"
            )

        print("═" * 88 + "\n")
