"""
Strategy Optimizer Script
Iterates through parameter combinations to find the best settings for active strategies.
"""

import subprocess
import yaml
import itertools
from pathlib import Path

# Config paths
CONFIG_PATH = Path("config/strategies.yaml")
RESULTS_DIR = Path("scratch/optimization_results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def run_backtest(symbol="BTC/USDT:USDT", days=30):
    """Run a backtest and return the results as a dict."""
    try:
        # We run the actual backtest script
        cmd = ["uv", "run", "backtest", "--symbol", symbol, "--days", str(days)]
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Parse output for Total Return
        # The backtest script prints: "Total Return: X.X%"
        output = result.stdout
        for line in output.split("\n"):
            if "Total Return:" in line:
                return_str = line.split("Total Return:")[1].strip().replace("%", "")
                return float(return_str)
            if "No data" in line:
                return -999.0
    except Exception as e:
        print(f"Error running backtest: {e}")
        return -999.0
    return -999.0


def update_config(strategy_name, params):
    """Updates the strategies.yaml with new params."""
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)

    if strategy_name in config:
        config[strategy_name].update(params)

    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(config, f)


def optimize_macd_bollinger():
    """Optimize parameters for MACD Bollinger."""
    print("Optimizing MACD Bollinger...")

    # Define search space
    search_space = {
        "bb_std_dev": [2.0, 2.2, 2.5],
        "stop_loss_atr_multiplier": [1.5, 2.0, 2.5],
    }

    keys, values = zip(*search_space.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]

    results = []

    # Save original config
    with open(CONFIG_PATH, "r") as f:
        original_config = f.read()

    try:
        for combo in combinations:
            print(f"Testing combo: {combo}")
            update_config("macd_bollinger", combo)
            total_return = run_backtest()
            results.append({"params": combo, "return": total_return})
            print(f"Result: {total_return}%")

    finally:
        # Restore original config
        with open(CONFIG_PATH, "w") as f:
            f.write(original_config)

    # Sort and show best
    sorted_results = sorted(results, key=lambda x: x["return"], reverse=True)
    print("\n--- MACD Bollinger Results ---")
    for r in sorted_results[:5]:
        print(f"Return: {r['return']}% | Params: {r['params']}")


if __name__ == "__main__":
    optimize_macd_bollinger()
