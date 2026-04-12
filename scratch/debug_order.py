import os
import ccxt
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("EXCHANGE_API_KEY")
secret_key = os.getenv("EXCHANGE_SECRET_KEY")

exchange = ccxt.bybit({
    "apiKey": api_key,
    "secret": secret_key,
    "options": {"defaultType": "swap"}
})
exchange.enable_demo_trading(True)

symbol = "BTC/USDT:USDT"
side = "buy"
order_type = "market"
amount = 0.01

try:
    print(f"Testing create_order with: symbol={symbol}, type={order_type}, side={side}, amount={amount}")
    # CCXT internally often calls .lower() on side and type
    # If one of these is None, it crashes.
    res = exchange.create_order(symbol, order_type, side, amount)
    print("Success!")
    print(res)
except Exception as e:
    print(f"Caught expected error: {e}")
    import traceback
    traceback.print_exc()
