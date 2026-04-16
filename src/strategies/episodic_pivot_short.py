"""
Strategy 15: Episodic Pivot Short (EPS) - Sudden Liquidity Crash

Mirror of the EP strategy, focusing on high-volume breakdowns 
characterized by massive selling pressure andstructural collapses.

Best in: Bearish capitulation and high-volatility sideways regimes.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import Signal, SignalType, Side, MarketRegime, StrategyType
from src.strategies.base import BaseStrategy


class EpisodicPivotShortStrategy(BaseStrategy):

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

    @property
    def name(self) -> str:
        return "episodic_pivot_short"

    def indicator_requirements(self) -> dict[str, object]:
        return {
            "ema": [20, 50, 200],
            "atr": [14],
            "adx": [14],
            "volume_sma": [20],
            "microstructure": True,
        }

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        regime: MarketRegime,
    ) -> Optional[Signal]:
        if len(df) < 200:
            return None

        curr = df.iloc[-1]
        
        # 1. Burst Detection
        vol_mult = float(self.params.get("vol_multiplier", 2.0))
        is_burst = curr.get("volume_burst_ratio", 0.0) > vol_mult
        if not is_burst:
            return None

        # 2. Structure Breakdown
        rolling_low_20 = df["low"].shift(1).rolling(20).min().iloc[-1]
        
        close = curr["close"]
        high = curr["high"]
        low = curr["low"]
        
        # 3. ADR (Average Daily Range) for Stop structure
        adr = (df["high"] - df["low"]).rolling(20).mean().iloc[-1]
        bars_move = high - low
        stop_buffer_atr = float(self.params.get("stop_buffer_atr", 0.1))
        atr = float(curr.get("atr", adr) or adr)
        
        tp_rr_ratio = float(self.params.get("tp_rr_ratio", 3.0))

        # 4. Short Execution
        # Trigger: Low < 20-bar Low + Volume Burst + Below EMA50
        if low < rolling_low_20 and close < df["ema_50"].iloc[-1]:
            # EPS Rule: Stop is the HIGH of the burst candle (Structure Stop)
            stop_loss = high + (atr * stop_buffer_atr)
            risk = stop_loss - close
            
            # Filter: Don't chase if the crash candle itself is > 1.5x ADR
            if bars_move > (adr * 1.5):
                return None
                
            if risk <= 0: return None
            
            take_profit = close - (risk * tp_rr_ratio)
            
            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                side=Side.SELL,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=0.85,
                regime=regime,
                reason=f"Episodic Short | Vol Burst: {curr['volume_burst_ratio']:.1f}x | Structural Break",
                metadata={
                    "volume_burst_ratio": curr["volume_burst_ratio"],
                    "breakout_low": rolling_low_20
                }
            )

        return None
