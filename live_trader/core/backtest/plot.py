"""
Backtesting Visualization Utilities.

Generates equity curve plots, drawdown charts, and parameter sensitivity heatmaps.
"""

from pathlib import Path
from datetime import datetime
from typing import List, Dict

import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np

from src.core.backtest.models import BacktestResult


def plot_equity_curve(result: BacktestResult, output_dir: str | Path, filename: str | None = None):
    """
    Plot the equity curve and underwater (drawdown) chart for a BacktestResult.
    Saves the plot as a PNG in the specified directory.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if filename is None:
        filename = f"{result.symbol.replace('/', '_')}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
        
    filepath = output_dir / filename
    
    if not result.trades or not result.equity_curve:
        return
        
    timestamps = [result.period_start] + [trade.exit_time for trade in result.trades]
    if len(timestamps) != len(result.equity_curve):
        # Fallback if there's a length mismatch
        timestamps = list(range(len(result.equity_curve)))
        
    eq_series = pd.Series(result.equity_curve, index=timestamps)
    rolling_max = eq_series.cummax()
    drawdowns = (eq_series - rolling_max) / rolling_max * 100
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
    
    # Plot 1: Equity Curve
    ax1.plot(eq_series.index, eq_series.values, color='#2962ff', linewidth=2)
    ax1.fill_between(eq_series.index, eq_series.values, eq_series.values[0], alpha=0.1, color='#2962ff')
    ax1.axhline(y=eq_series.values[0], color='r', linestyle='--', alpha=0.5)
    ax1.set_title(f"Equity Curve - {result.symbol}", fontsize=14, fontweight='bold')
    ax1.set_ylabel("Equity ($)", fontsize=12)
    ax1.grid(True, alpha=0.3)
    
    # Add key metrics text box
    metrics_text = (
        f"ROI: {result.net_roi_pct:+.2f}%\n"
        f"Win Rate: {result.win_rate_pct:.1f}%\n"
        f"Trades: {len(result.trades)}\n"
        f"Sharpe: {result.sharpe_ratio:.2f}\n"
        f"Sortino: {result.sortino_ratio:.2f}\n"
        f"Max DD: {result.max_drawdown_pct:.2f}%\n"
        f"MC 95% DD: {result.mc_worst_95_mdd_pct:.2f}%\n"
        f"Underwater: {result.max_drawdown_duration_days:.1f}d"
    )
    ax1.text(0.02, 0.95, metrics_text, transform=ax1.transAxes, fontsize=10,
             verticalalignment='top', bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    # Plot 2: Drawdown (Underwater)
    ax2.fill_between(drawdowns.index, drawdowns.values, 0, color='#ff5252', alpha=0.5)
    ax2.plot(drawdowns.index, drawdowns.values, color='#ff1744', linewidth=1)
    ax2.set_ylabel("Drawdown (%)", fontsize=12)
    ax2.set_xlabel("Date", fontsize=12)
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(filepath, dpi=150, bbox_inches='tight')
    plt.close()
    
    return filepath


def plot_parameter_heatmap(
    grid_results: List[Dict], 
    output_dir: str | Path, 
    filename: str,
    metric_name: str = "Sortino Ratio"
):
    """
    Plot a heatmap of parameter grid search results.
    Assumes there are exactly 2 parameters being optimized. If there are >2,
    it picks the two with the most unique values, or just the first two.
    """
    if not grid_results:
        return
        
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename
    
    # Extract parameter keys dynamically
    param_keys = list(grid_results[0]["params"].keys())
    if len(param_keys) < 2:
        # Not enough parameters to plot a 2D heatmap
        return
        
    # Find the two parameters with the highest cardinality
    cardinality = {}
    for k in param_keys:
        cardinality[k] = len(set(str(r["params"][k]) for r in grid_results))
        
    sorted_keys = sorted(param_keys, key=lambda k: cardinality[k], reverse=True)
    x_param = sorted_keys[0]
    y_param = sorted_keys[1]
    
    # Prepare dataframe
    data = []
    for r in grid_results:
        data.append({
            x_param: r["params"][x_param],
            y_param: r["params"][y_param],
            "metric": r["metric"]
        })
        
    df = pd.DataFrame(data)
    
    # Pivot for heatmap
    # If there are >2 parameters, we take the mean of the metric for the x/y pair
    pivot_df = df.pivot_table(index=y_param, columns=x_param, values="metric", aggfunc=np.mean)
    
    plt.figure(figsize=(10, 8))
    sns.heatmap(
        pivot_df, 
        annot=True, 
        cmap="RdYlGn", 
        fmt=".2f", 
        center=0,
        cbar_kws={'label': metric_name}
    )
    plt.title(f"Parameter Sensitivity ({metric_name})", fontsize=14, pad=15)
    plt.ylabel(y_param, fontsize=12)
    plt.xlabel(x_param, fontsize=12)
    
    plt.tight_layout()
    plt.savefig(filepath, dpi=150, bbox_inches='tight')
    plt.close()
    
    return filepath
