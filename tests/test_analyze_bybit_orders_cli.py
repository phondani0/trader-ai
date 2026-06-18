from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.cli import analyze_bybit_orders  # noqa: E402


class AnalyzeBybitOrdersCliTest(unittest.TestCase):
    def test_load_runtime_env_reads_dotenv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "TRADING_MODE=paper\n"
                "EXCHANGE_ID=bybit\n"
                "EXCHANGE_API_KEY=key\n"
                "EXCHANGE_SECRET_KEY=secret\n",
            )
            with patch.dict(os.environ, {}, clear=True):
                analyze_bybit_orders.load_runtime_env(env_path)

                self.assertEqual(os.environ["TRADING_MODE"], "paper")
                self.assertEqual(os.environ["EXCHANGE_ID"], "bybit")
                self.assertEqual(os.environ["EXCHANGE_API_KEY"], "key")
                self.assertEqual(os.environ["EXCHANGE_SECRET_KEY"], "secret")

    def test_build_exchange_uses_demo_url_for_paper_mode_by_default(self):
        exchange = Mock()
        exchange.id = "bybit"
        exchange.urls = {"api": {"private": "old", "public": "old"}}
        exchange_class = Mock(return_value=exchange)

        with patch.dict(
            os.environ,
            {
                "TRADING_MODE": "paper",
                "EXCHANGE_ID": "bybit",
                "EXCHANGE_API_KEY": "key",
                "EXCHANGE_SECRET_KEY": "secret",
            },
            clear=True,
        ), patch.object(analyze_bybit_orders.ccxt, "bybit", exchange_class):
            result = analyze_bybit_orders.build_exchange()

        self.assertIs(result, exchange)
        exchange_class.assert_called_once()
        exchange.set_sandbox_mode.assert_not_called()
        self.assertEqual(
            exchange.urls["api"],
            {
                "private": analyze_bybit_orders.BYBIT_DEMO_REST_URL,
                "public": analyze_bybit_orders.BYBIT_DEMO_REST_URL,
            },
        )

    def test_build_exchange_can_use_testnet_sandbox_mode(self):
        exchange = Mock()
        exchange.id = "bybit"
        exchange.urls = {"api": {"private": "old"}}
        exchange_class = Mock(return_value=exchange)

        with patch.dict(
            os.environ,
            {
                "TRADING_MODE": "paper",
                "EXCHANGE_ID": "bybit",
                "EXCHANGE_API_KEY": "key",
                "EXCHANGE_SECRET_KEY": "secret",
            },
            clear=True,
        ), patch.object(analyze_bybit_orders.ccxt, "bybit", exchange_class):
            result = analyze_bybit_orders.build_exchange(account_mode="testnet")

        self.assertIs(result, exchange)
        exchange.set_sandbox_mode.assert_called_once_with(True)

    def test_render_report_flags_small_orders_and_short_holds(self):
        report = analyze_bybit_orders.AnalysisReport(
            exchange_id="bybit",
            trading_mode="paper",
            since_hours=24,
            positions=[],
            open_orders=[],
            closed_orders=[
                analyze_bybit_orders.OrderSummary(
                    symbol="ETH/USDT:USDT",
                    order_id="order-1",
                    client_order_id="client-1",
                    side="sell",
                    order_type="market",
                    status="closed",
                    reduce_only=True,
                    amount=0.01,
                    filled=0.01,
                    average_price=2000.0,
                    notional_usdt=20.0,
                    timestamp=1_800_000,
                    stop_trigger_price=None,
                    raw_order_type="Market",
                    raw_stop_order_type="",
                )
            ],
            fills=[],
            closed_pnl=[
                analyze_bybit_orders.ClosedPnlSummary(
                    symbol="ETHUSDT",
                    side="Sell",
                    quantity=0.01,
                    entry_price=2000.0,
                    exit_price=1995.0,
                    closed_pnl_usdt=0.05,
                    created_time=0,
                    updated_time=1_800_000,
                    hold_minutes=30.0,
                )
            ],
            warnings=[],
        )

        output = analyze_bybit_orders.render_report(report)

        self.assertIn("small filled orders under 100 USDT: 1", output)
        self.assertIn("inferred round trips with hold < 60m: 0", output)
        self.assertIn("filled reduce-only closed orders: 1", output)

    def test_render_report_flags_open_positions_without_protective_stop_orders(self):
        report = analyze_bybit_orders.AnalysisReport(
            exchange_id="bybit",
            trading_mode="paper",
            since_hours=24,
            positions=[
                analyze_bybit_orders.PositionSummary(
                    symbol="ETHUSDT",
                    side="Sell",
                    contracts=0.08,
                    entry_price=1751.58,
                    mark_price=1706.35,
                    notional_usdt=140.13,
                    unrealized_pnl_usdt=3.62,
                    liquidation_price=None,
                )
            ],
            open_orders=[],
            closed_orders=[],
            fills=[],
            closed_pnl=[],
            warnings=[],
        )

        output = analyze_bybit_orders.render_report(report)

        self.assertIn("unprotected open positions: 1", output)
        self.assertIn("ETHUSDT Sell qty=0.08 notional=140.13", output)

    def test_reduce_only_trigger_order_protects_matching_open_position(self):
        report = analyze_bybit_orders.AnalysisReport(
            exchange_id="bybit",
            trading_mode="paper",
            since_hours=24,
            positions=[
                analyze_bybit_orders.PositionSummary(
                    symbol="ETHUSDT",
                    side="Sell",
                    contracts=0.08,
                    entry_price=1751.58,
                    mark_price=1706.35,
                    notional_usdt=140.13,
                    unrealized_pnl_usdt=3.62,
                    liquidation_price=None,
                )
            ],
            open_orders=[
                analyze_bybit_orders.OrderSummary(
                    symbol="ETH/USDT:USDT",
                    order_id="stop-1",
                    client_order_id="client-stop-1",
                    side="Buy",
                    order_type="Market",
                    status="open",
                    reduce_only=True,
                    amount=0.08,
                    filled=0.0,
                    average_price=0.0,
                    notional_usdt=0.0,
                    timestamp=1000,
                    stop_trigger_price=1800.0,
                    raw_order_type="Market",
                    raw_stop_order_type="Stop",
                )
            ],
            closed_orders=[],
            fills=[],
            closed_pnl=[],
            warnings=[],
        )

        output = analyze_bybit_orders.render_report(report)

        self.assertIn("unprotected open positions: 0", output)
        self.assertEqual(analyze_bybit_orders._find_unprotected_positions(report), [])

    def test_analyze_exchange_fetches_read_only_data(self):
        exchange = SimpleNamespace(
            id="bybit",
            fetch_positions=Mock(return_value=[]),
            fetch_open_orders=Mock(return_value=[]),
            fetch_closed_orders=Mock(return_value=[]),
            fetch_my_trades=Mock(return_value=[]),
        )

        with patch.dict(os.environ, {"TRADING_MODE": "paper"}, clear=True):
            report = analyze_bybit_orders.analyze_exchange(
                exchange,
                since_hours=1,
                limit=10,
                symbols=["ETH/USDT:USDT"],
            )

        self.assertEqual(report.exchange_id, "bybit")
        exchange.fetch_positions.assert_called_once_with()
        exchange.fetch_open_orders.assert_called_once_with("ETH/USDT:USDT")
        exchange.fetch_closed_orders.assert_called_once()
        exchange.fetch_my_trades.assert_called_once()
        self.assertFalse(hasattr(exchange, "create_order"))

    def test_bybit_open_order_fallback_fetches_conditional_stop_orders(self):
        def realtime(params):
            if params.get("orderFilter") == "StopOrder":
                return {
                    "result": {
                        "list": [
                            {
                                "symbol": "ETHUSDT",
                                "orderId": "stop-1",
                                "orderLinkId": "client-stop-1",
                                "side": "Buy",
                                "orderType": "Market",
                                "orderStatus": "New",
                                "reduceOnly": "true",
                                "qty": "0.1",
                                "cumExecQty": "0",
                                "price": "0",
                                "triggerPrice": "1800",
                                "stopOrderType": "Stop",
                                "createdTime": "1000",
                            }
                        ]
                    }
                }
            return {
                "result": {
                    "list": [
                        {
                            "symbol": "ETHUSDT",
                            "orderId": "regular-1",
                            "orderLinkId": "client-regular-1",
                            "side": "Sell",
                            "orderType": "Limit",
                            "orderStatus": "New",
                            "reduceOnly": "false",
                            "qty": "0.1",
                            "cumExecQty": "0",
                            "price": "1900",
                            "triggerPrice": "0",
                            "stopOrderType": "UNKNOWN",
                            "createdTime": "900",
                        }
                    ]
                }
            }

        exchange = SimpleNamespace(privateGetV5OrderRealtime=Mock(side_effect=realtime))

        orders = analyze_bybit_orders._fetch_bybit_orders(
            exchange,
            [],
            symbol="ETH/USDT:USDT",
            status="open",
            since_ms=None,
            limit=50,
        )

        order_ids = {order.order_id for order in orders}
        self.assertEqual(order_ids, {"regular-1", "stop-1"})
        self.assertEqual(exchange.privateGetV5OrderRealtime.call_count, 2)
        self.assertIn(
            {
                "category": "linear",
                "openOnly": 0,
                "settleCoin": "USDT",
                "symbol": "ETHUSDT",
                "orderFilter": "StopOrder",
            },
            [call.args[0] for call in exchange.privateGetV5OrderRealtime.call_args_list],
        )


if __name__ == "__main__":
    unittest.main()
