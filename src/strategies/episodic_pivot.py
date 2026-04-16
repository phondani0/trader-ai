"""
Strategy 14: Episodic Pivot (EP) - Sudden Momentum Burst

Focuses on the "Top 0.1%" high-quality momentum setups: 
sudden 300%+ volume spikes breaking long-term consolidation.

Best in: Early breakout regimes and emerging trends.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.core.models import Signal, SignalType, Side, MarketRegime, StrategyType
from src.strategies.base import BaseStrategy


class EpisodicPivotStrategy(BaseStrategy):

    @property
    def strategy_type(self) -> StrategyType:
        return StrategyType.TREND_FOLLOWING

    @property
    def name(self) -> str:
        return "episodic_pivot"

    def indicator_requirements(self) -> dict[str, object]:
        # Uses standard indicators plus microstructure fields
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
        # Need enough data for EMA200
        if len(df) < 200:
            return None

        curr = df.iloc[-1]

        # 1. Burst Detection (The "Sudden Momentum" Pillar)
        vol_mult = float(self.params.get("vol_multiplier", 2.0))
        is_burst = curr.get("volume_burst_ratio", 0.0) > vol_mult
        if not is_burst:
            return None

        # 2. Breakout Detection
        # Check if current high/low breaks the previous 20-bar range
        rolling_high_20 = df["high"].shift(1).rolling(20).max().iloc[-1]
        
        close = curr["close"]
        high = curr["high"]
        low = curr["low"]
        adx = float(curr.get("adx", 0.0) or 0.0)
        
        # 3. ADR (Average Daily Range) Filter - Prevent chasing "Exhausted" moves
        # Calculate ADR over 20 bars (normalized to the timeframe)
        adr = (df["high"] - df["low"]).rolling(20).mean().iloc[-1]
        bars_move = high - low
        stop_buffer_atr = float(self.params.get("stop_buffer_atr", 0.1))
        atr = float(curr.get("atr", adr) or adr)
        
        # Risk Management Params
        tp_rr_ratio = float(self.params.get("tp_rr_ratio", 3.0))

        # 4. Directional Logic
        # LONG: Volume Burst + High > rolling_high + ADX > 20
        if high > rolling_high_20 and close > df["ema_50"].iloc[-1]:
            # EP Rule: Stop is the LOW of the burst candle (Structure Stop)
            stop_loss = low - (atr * stop_buffer_atr)
            risk = close - stop_loss
            
            # Filter: Don't chase if the burst candle itself is > 1.5x ADR
            if bars_move > (adr * 1.5):
                return None
                
            if risk <= 0: return None
            
            take_profit = close + (risk * tp_rr_ratio)
            
            return self._create_signal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                side=Side.BUY,
                entry_price=close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=0.85,  # Bursts are high conviction
                regime=regime,
                reason=f"Episodic Pivot Long | Vol Burst: {curr['volume_burst_ratio']:.1f}x | Range Breakout",
                metadata={
                    "volume_burst_ratio": curr["volume_burst_ratio"],
                    "adx": adx,
                    "breakout_high": rolling_high_20
                }
            )

        # SHORT: Disabling short logic in the Long-Only strategy class
        # We will handle shorts in EpisodicPivotShort for better benchmark isolation
        return None
