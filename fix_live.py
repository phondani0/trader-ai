from pathlib import Path
content = Path("src/trader_ai/runners/live.py").read_text()

# Cut out node_config
# Instead of doing it with regex, I will just write a script to rewrite live.py
