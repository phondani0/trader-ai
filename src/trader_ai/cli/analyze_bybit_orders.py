from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ccxt
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BYBIT_DEMO_REST_URL = "https://api-demo.bybit.com"


@dataclass(frozen=True)
class PositionSummary:
    symbol: str
    side: str
    contracts: float
    entry_price: float
    mark_price: float
    notional_usdt: float
    unrealized_pnl_usdt: float
    liquidation_price: float | None


@dataclass(frozen=True)
class OrderSummary:
    symbol: str
    order_id: str
    client_order_id: str
    side: str
    order_type: str
    status: str
    reduce_only: bool
    amount: float
    filled: float
    average_price: float
    notional_usdt: float
    timestamp: int | None
    stop_trigger_price: float | None
    raw_order_type: str
    raw_stop_order_type: str


@dataclass(frozen=True)
class FillSummary:
    symbol: str
    order_id: str
    side: str
    amount: float
    price: float
    notional_usdt: float
    fee_usdt: float
    timestamp: int | None
    liquidity: str


@dataclass(frozen=True)
class ClosedPnlSummary:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    closed_pnl_usdt: float
    created_time: int | None
    updated_time: int | None
    hold_minutes: float | None


@dataclass(frozen=True)
class AnalysisReport:
    exchange_id: str
    trading_mode: str
    since_hours: int
    positions: list[PositionSummary]
    open_orders: list[OrderSummary]
    closed_orders: list[OrderSummary]
    fills: list[FillSummary]
    closed_pnl: list[ClosedPnlSummary]
    warnings: list[str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Bybit order/position analyzer for trader-ai",
    )
    parser.add_argument("--env", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--since-hours", type=int, default=72)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument(
        "--account-mode",
        choices=["env", "demo", "testnet", "live"],
        default="env",
        help=(
            "Bybit account endpoint to use. env reads BYBIT_ACCOUNT_MODE or "
            "EXCHANGE_REST_URL; paper defaults to demo."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--fail-unprotected-positions",
        action="store_true",
        help=(
            "Exit non-zero when any open position has no open reduce-only "
            "stop/trigger order on the same symbol."
        ),
    )
    return parser


def load_runtime_env(env_path: str | Path) -> None:
    load_dotenv(env_path)


def build_exchange(*, account_mode: str = "env") -> ccxt.Exchange:
    exchange_id = os.environ.get("EXCHANGE_ID", "bybit")
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class(
        {
            "apiKey": os.environ.get("EXCHANGE_API_KEY", ""),
            "secret": os.environ.get("EXCHANGE_SECRET_KEY", ""),
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "defaultSubType": "linear",
            },
        }
    )
    trading_mode = os.environ.get("TRADING_MODE", "paper").lower()

    rest_url = os.environ.get("EXCHANGE_REST_URL", "")
    resolved_account_mode = _resolve_account_mode(
        requested_mode=account_mode,
        trading_mode=trading_mode,
        rest_url=rest_url,
    )
    if rest_url:
        _set_exchange_api_url(exchange, rest_url)
    elif exchange_id == "bybit" and resolved_account_mode == "demo":
        _set_exchange_api_url(exchange, BYBIT_DEMO_REST_URL)
    elif resolved_account_mode == "testnet" and hasattr(exchange, "set_sandbox_mode"):
        exchange.set_sandbox_mode(True)
    return exchange


def analyze_exchange(
    exchange: ccxt.Exchange,
    *,
    since_hours: int,
    limit: int,
    symbols: list[str],
) -> AnalysisReport:
    warnings: list[str] = []
    since_ms = int(time.time() * 1000) - since_hours * 60 * 60 * 1000
    selected_symbols = symbols or [None]

    positions = _fetch_positions(exchange, warnings)
    open_orders: list[OrderSummary] = []
    closed_orders: list[OrderSummary] = []
    fills: list[FillSummary] = []
    closed_pnl: list[ClosedPnlSummary] = []

    for symbol in selected_symbols:
        open_orders.extend(_fetch_orders(exchange, warnings, symbol, "open"))
        closed_orders.extend(_fetch_orders(exchange, warnings, symbol, "closed", since_ms, limit))
        fills.extend(_fetch_fills(exchange, warnings, symbol, since_ms, limit))
        closed_pnl.extend(_fetch_closed_pnl(exchange, warnings, symbol, since_ms, limit))

    return AnalysisReport(
        exchange_id=exchange.id,
        trading_mode=os.environ.get("TRADING_MODE", "paper").lower(),
        since_hours=since_hours,
        positions=positions,
        open_orders=open_orders,
        closed_orders=closed_orders,
        fills=fills,
        closed_pnl=closed_pnl,
        warnings=warnings,
    )


def render_report(report: AnalysisReport) -> str:
    lines = [
        f"Exchange: {report.exchange_id}",
        f"Mode: {report.trading_mode}",
        f"Window: last {report.since_hours}h",
        "",
        "Open positions:",
    ]
    if not report.positions:
        lines.append("- none")
    for position in sorted(report.positions, key=lambda item: item.symbol):
        lines.append(
            "- "
            f"{position.symbol} {position.side} qty={position.contracts:g} "
            f"notional={position.notional_usdt:.2f} entry={position.entry_price:g} "
            f"mark={position.mark_price:g} upnl={position.unrealized_pnl_usdt:.2f}"
        )

    lines.extend(["", "Open orders:"])
    if not report.open_orders:
        lines.append("- none")
    for order in sorted(report.open_orders, key=lambda item: (item.symbol, item.timestamp or 0)):
        role = "reduce-only" if order.reduce_only else "entry/increase"
        trigger = f" trigger={order.stop_trigger_price:g}" if order.stop_trigger_price else ""
        lines.append(
            "- "
            f"{order.symbol} {order.side} {order.order_type}/{order.raw_order_type} "
            f"{role} amount={order.amount:g} filled={order.filled:g} "
            f"notional={order.notional_usdt:.2f}{trigger} id={order.client_order_id or order.order_id}"
        )

    lines.extend(["", "Recent closed orders:"])
    if not report.closed_orders:
        lines.append("- none")
    for order in sorted(report.closed_orders, key=lambda item: item.timestamp or 0, reverse=True)[:40]:
        role = "reduce-only" if order.reduce_only else "entry/increase"
        lines.append(
            "- "
            f"{_format_time(order.timestamp)} {order.symbol} {order.side} "
            f"{order.order_type}/{order.raw_order_type} {order.status} {role} "
            f"filled={order.filled:g} avg={order.average_price:g} notional={order.notional_usdt:.2f} "
            f"id={order.client_order_id or order.order_id}"
        )

    lines.extend(["", "Recent fills by symbol:"])
    fills_by_symbol: dict[str, list[FillSummary]] = defaultdict(list)
    for fill in report.fills:
        fills_by_symbol[fill.symbol].append(fill)
    if not fills_by_symbol:
        lines.append("- none")
    for symbol, fills in sorted(fills_by_symbol.items()):
        notional = sum(fill.notional_usdt for fill in fills)
        fees = sum(fill.fee_usdt for fill in fills)
        lines.append(
            f"- {symbol}: fills={len(fills)} notional={notional:.2f} fees={fees:.4f}"
        )

    lines.extend(["", "Closed PnL:"])
    if not report.closed_pnl:
        lines.append("- none")
    for item in sorted(report.closed_pnl, key=lambda row: row.updated_time or 0, reverse=True)[:40]:
        hold = f" hold={item.hold_minutes:.1f}m" if item.hold_minutes is not None else ""
        lines.append(
            "- "
            f"{_format_time(item.updated_time)} {item.symbol} {item.side} "
            f"qty={item.quantity:g} entry={item.entry_price:g} exit={item.exit_price:g} "
            f"pnl={item.closed_pnl_usdt:.4f}{hold}"
        )

    lines.extend(["", "Diagnostics:"])
    lines.extend(_render_diagnostics(report))

    if report.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in report.warnings)

    return "\n".join(lines)


def _fetch_positions(exchange: ccxt.Exchange, warnings: list[str]) -> list[PositionSummary]:
    try:
        raw_positions = exchange.fetch_positions()
    except Exception as exc:
        fallback = _fetch_bybit_positions(exchange, warnings)
        if fallback:
            warnings.append("fetch_positions failed; used Bybit v5 position/list fallback")
            return fallback
        warnings.append(f"fetch_positions failed: {exc}")
        return []

    positions = []
    for position in raw_positions:
        contracts = _float(position.get("contracts") or position.get("contractSize"))
        if contracts <= 0:
            continue
        entry_price = _float(position.get("entryPrice"))
        mark_price = _float(position.get("markPrice") or position.get("lastPrice"))
        notional = _float(position.get("notional")) or contracts * mark_price
        positions.append(
            PositionSummary(
                symbol=str(position.get("symbol") or ""),
                side=str(position.get("side") or ""),
                contracts=contracts,
                entry_price=entry_price,
                mark_price=mark_price,
                notional_usdt=abs(notional),
                unrealized_pnl_usdt=_float(position.get("unrealizedPnl")),
                liquidation_price=(
                    _float(position.get("liquidationPrice"))
                    if position.get("liquidationPrice") is not None
                    else None
                ),
            )
        )
    return positions


def _resolve_account_mode(
    *,
    requested_mode: str,
    trading_mode: str,
    rest_url: str,
) -> str:
    if rest_url:
        return "custom"
    if requested_mode != "env":
        return requested_mode
    env_mode = os.environ.get("BYBIT_ACCOUNT_MODE", "").lower()
    if env_mode in {"demo", "testnet", "live"}:
        return env_mode
    return "live" if trading_mode == "live" else "demo"


def _set_exchange_api_url(exchange: ccxt.Exchange, rest_url: str) -> None:
    api_urls = exchange.urls.get("api")
    if isinstance(api_urls, dict):
        for key in list(api_urls):
            api_urls[key] = rest_url
    else:
        exchange.urls["api"] = rest_url


def _fetch_orders(
    exchange: ccxt.Exchange,
    warnings: list[str],
    symbol: str | None,
    status: str,
    since_ms: int | None = None,
    limit: int | None = None,
) -> list[OrderSummary]:
    try:
        if status == "open":
            raw_orders = exchange.fetch_open_orders(symbol)
        else:
            raw_orders = exchange.fetch_closed_orders(symbol, since=since_ms, limit=limit)
    except Exception as exc:
        fallback = _fetch_bybit_orders(
            exchange,
            warnings,
            symbol=symbol,
            status=status,
            since_ms=since_ms,
            limit=limit,
        )
        if fallback:
            warnings.append(f"fetch_{status}_orders failed; used Bybit v5 fallback")
            return fallback
        label = symbol or "all symbols"
        warnings.append(f"fetch_{status}_orders failed for {label}: {exc}")
        return []
    return [_summarize_order(order) for order in raw_orders]


def _fetch_fills(
    exchange: ccxt.Exchange,
    warnings: list[str],
    symbol: str | None,
    since_ms: int,
    limit: int,
) -> list[FillSummary]:
    try:
        raw_fills = exchange.fetch_my_trades(symbol, since=since_ms, limit=limit)
    except Exception as exc:
        fallback = _fetch_bybit_fills(
            exchange,
            warnings,
            symbol=symbol,
            since_ms=since_ms,
            limit=limit,
        )
        if fallback:
            warnings.append("fetch_my_trades failed; used Bybit v5 execution/list fallback")
            return fallback
        label = symbol or "all symbols"
        warnings.append(f"fetch_my_trades failed for {label}: {exc}")
        return []
    return [_summarize_fill(fill) for fill in raw_fills]


def _fetch_closed_pnl(
    exchange: ccxt.Exchange,
    warnings: list[str],
    symbol: str | None,
    since_ms: int,
    limit: int,
) -> list[ClosedPnlSummary]:
    method = getattr(exchange, "privateGetV5PositionClosedPnl", None)
    if method is None:
        return []

    params: dict[str, Any] = {
        "category": "linear",
        "startTime": since_ms,
        "limit": min(limit, 100),
    }
    if symbol:
        market_symbol = symbol.replace("/", "").replace(":USDT", "")
        params["symbol"] = market_symbol

    try:
        payload = method(params)
    except Exception as exc:
        label = symbol or "all symbols"
        warnings.append(f"closed PnL fetch failed for {label}: {exc}")
        return []

    rows = payload.get("result", {}).get("list", [])
    return [_summarize_closed_pnl(row) for row in rows]


def _summarize_order(order: dict[str, Any]) -> OrderSummary:
    info = order.get("info", {}) or {}
    amount = _float(order.get("amount"))
    filled = _float(order.get("filled"))
    average_price = _float(order.get("average") or info.get("avgPrice"))
    price = average_price or _float(order.get("price") or info.get("price"))
    stop_trigger_price = _float(
        order.get("triggerPrice")
        or order.get("stopPrice")
        or info.get("triggerPrice")
        or info.get("stopLoss")
    )
    return OrderSummary(
        symbol=str(order.get("symbol") or info.get("symbol") or ""),
        order_id=str(order.get("id") or info.get("orderId") or ""),
        client_order_id=str(order.get("clientOrderId") or info.get("orderLinkId") or ""),
        side=str(order.get("side") or info.get("side") or ""),
        order_type=str(order.get("type") or info.get("orderType") or ""),
        status=str(order.get("status") or info.get("orderStatus") or ""),
        reduce_only=(
            bool(order.get("reduceOnly"))
            or info.get("reduceOnly") is True
            or str(info.get("reduceOnly")).lower() == "true"
        ),
        amount=amount,
        filled=filled,
        average_price=average_price,
        notional_usdt=abs(filled or amount) * price,
        timestamp=order.get("timestamp") or _int_or_none(info.get("createdTime")),
        stop_trigger_price=stop_trigger_price or None,
        raw_order_type=str(info.get("orderType") or ""),
        raw_stop_order_type=str(info.get("stopOrderType") or ""),
    )


def _fetch_bybit_positions(
    exchange: ccxt.Exchange,
    warnings: list[str],
) -> list[PositionSummary]:
    method = getattr(exchange, "privateGetV5PositionList", None)
    if method is None:
        return []
    try:
        payload = method({"category": "linear", "settleCoin": "USDT"})
    except Exception as exc:
        warnings.append(f"Bybit v5 position/list fallback failed: {exc}")
        return []
    rows = payload.get("result", {}).get("list", [])
    positions: list[PositionSummary] = []
    for row in rows:
        contracts = _float(row.get("size"))
        if contracts <= 0:
            continue
        positions.append(
            PositionSummary(
                symbol=str(row.get("symbol") or ""),
                side=str(row.get("side") or ""),
                contracts=contracts,
                entry_price=_float(row.get("avgPrice")),
                mark_price=_float(row.get("markPrice")),
                notional_usdt=abs(_float(row.get("positionValue"))),
                unrealized_pnl_usdt=_float(row.get("unrealisedPnl")),
                liquidation_price=(
                    _float(row.get("liqPrice")) if row.get("liqPrice") not in (None, "") else None
                ),
            )
        )
    return positions


def _fetch_bybit_orders(
    exchange: ccxt.Exchange,
    warnings: list[str],
    *,
    symbol: str | None,
    status: str,
    since_ms: int | None,
    limit: int | None,
) -> list[OrderSummary]:
    if status == "open":
        method = getattr(exchange, "privateGetV5OrderRealtime", None)
        params: dict[str, Any] = {
            "category": "linear",
            "openOnly": 0,
            "settleCoin": "USDT",
        }
    else:
        method = getattr(exchange, "privateGetV5OrderHistory", None)
        params = {"category": "linear", "limit": min(limit or 50, 50)}
        if since_ms is not None:
            params["startTime"] = since_ms
    if method is None:
        return []
    if symbol:
        params["symbol"] = _bybit_market_symbol(symbol)
    if status == "open":
        rows = _fetch_bybit_open_order_rows(method, params, warnings)
        return [_summarize_bybit_order(row) for row in rows]
    try:
        payload = method(params)
    except Exception as exc:
        warnings.append(f"Bybit v5 order fallback failed: {exc}")
        return []
    rows = payload.get("result", {}).get("list", [])
    return [_summarize_bybit_order(row) for row in rows]


def _fetch_bybit_open_order_rows(
    method,
    params: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order_filter in (None, "StopOrder"):
        request_params = dict(params)
        if order_filter is not None:
            request_params["orderFilter"] = order_filter
        try:
            payload = method(request_params)
        except Exception as exc:
            label = order_filter or "all"
            warnings.append(f"Bybit v5 open order fallback failed for {label}: {exc}")
            continue
        rows.extend(payload.get("result", {}).get("list", []))
    return _dedupe_bybit_order_rows(rows)


def _dedupe_bybit_order_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_order_ids: set[str] = set()
    for row in rows:
        order_id = str(row.get("orderId") or "")
        if order_id and order_id in seen_order_ids:
            continue
        if order_id:
            seen_order_ids.add(order_id)
        deduped.append(row)
    return deduped


def _fetch_bybit_fills(
    exchange: ccxt.Exchange,
    warnings: list[str],
    *,
    symbol: str | None,
    since_ms: int,
    limit: int,
) -> list[FillSummary]:
    method = getattr(exchange, "privateGetV5ExecutionList", None)
    if method is None:
        return []
    params: dict[str, Any] = {
        "category": "linear",
        "startTime": since_ms,
        "limit": min(limit, 100),
    }
    if symbol:
        params["symbol"] = _bybit_market_symbol(symbol)
    try:
        payload = method(params)
    except Exception as exc:
        warnings.append(f"Bybit v5 execution/list fallback failed: {exc}")
        return []
    rows = payload.get("result", {}).get("list", [])
    return [_summarize_bybit_fill(row) for row in rows]


def _summarize_bybit_order(row: dict[str, Any]) -> OrderSummary:
    amount = _float(row.get("qty"))
    filled = _float(row.get("cumExecQty"))
    average_price = _float(row.get("avgPrice"))
    price = average_price or _float(row.get("price"))
    return OrderSummary(
        symbol=str(row.get("symbol") or ""),
        order_id=str(row.get("orderId") or ""),
        client_order_id=str(row.get("orderLinkId") or ""),
        side=str(row.get("side") or ""),
        order_type=str(row.get("orderType") or ""),
        status=str(row.get("orderStatus") or ""),
        reduce_only=str(row.get("reduceOnly")).lower() == "true",
        amount=amount,
        filled=filled,
        average_price=average_price,
        notional_usdt=abs(filled or amount) * price,
        timestamp=_int_or_none(row.get("createdTime")),
        stop_trigger_price=_float(row.get("triggerPrice")) or None,
        raw_order_type=str(row.get("orderType") or ""),
        raw_stop_order_type=str(row.get("stopOrderType") or ""),
    )


def _summarize_bybit_fill(row: dict[str, Any]) -> FillSummary:
    amount = _float(row.get("execQty"))
    price = _float(row.get("execPrice"))
    return FillSummary(
        symbol=str(row.get("symbol") or ""),
        order_id=str(row.get("orderId") or ""),
        side=str(row.get("side") or ""),
        amount=amount,
        price=price,
        notional_usdt=abs(amount * price),
        fee_usdt=_float(row.get("execFee")),
        timestamp=_int_or_none(row.get("execTime")),
        liquidity=str(row.get("execType") or row.get("isMaker") or ""),
    )


def _summarize_fill(fill: dict[str, Any]) -> FillSummary:
    info = fill.get("info", {}) or {}
    amount = _float(fill.get("amount") or info.get("execQty"))
    price = _float(fill.get("price") or info.get("execPrice"))
    fee = fill.get("fee") or {}
    fee_cost = _float(fee.get("cost") if isinstance(fee, dict) else None)
    return FillSummary(
        symbol=str(fill.get("symbol") or info.get("symbol") or ""),
        order_id=str(fill.get("order") or info.get("orderId") or ""),
        side=str(fill.get("side") or info.get("side") or ""),
        amount=amount,
        price=price,
        notional_usdt=abs(amount * price),
        fee_usdt=fee_cost,
        timestamp=fill.get("timestamp") or _int_or_none(info.get("execTime")),
        liquidity=str(info.get("execType") or info.get("isMaker") or ""),
    )


def _summarize_closed_pnl(row: dict[str, Any]) -> ClosedPnlSummary:
    created_time = _int_or_none(row.get("createdTime"))
    updated_time = _int_or_none(row.get("updatedTime"))
    hold_minutes = None
    if (
        created_time is not None
        and updated_time is not None
        and updated_time > created_time
    ):
        hold_minutes = (updated_time - created_time) / 60000
    return ClosedPnlSummary(
        symbol=str(row.get("symbol") or ""),
        side=str(row.get("side") or ""),
        quantity=_float(row.get("qty")),
        entry_price=_float(row.get("avgEntryPrice")),
        exit_price=_float(row.get("avgExitPrice")),
        closed_pnl_usdt=_float(row.get("closedPnl")),
        created_time=created_time,
        updated_time=updated_time,
        hold_minutes=hold_minutes,
    )


def _render_diagnostics(report: AnalysisReport) -> list[str]:
    lines: list[str] = []
    all_orders = [*report.open_orders, *report.closed_orders]
    round_trips = _infer_round_trips(report.closed_orders)
    small_orders = [
        order
        for order in all_orders
        if order.filled > 0 and 0 < order.notional_usdt < 100
    ]
    very_short_round_trips = [item for item in round_trips if item["hold_minutes"] < 60]
    reduce_only_closed = [
        order
        for order in report.closed_orders
        if order.reduce_only and order.filled > 0
    ]
    stop_like_orders = [
        order
        for order in all_orders
        if order.raw_stop_order_type or "stop" in order.order_type.lower() or order.stop_trigger_price
    ]

    lines.append(f"- small filled orders under 100 USDT: {len(small_orders)}")
    lines.append(f"- inferred round trips: {len(round_trips)}")
    lines.append(f"- inferred round trips with hold < 60m: {len(very_short_round_trips)}")
    lines.append(f"- filled reduce-only closed orders: {len(reduce_only_closed)}")
    lines.append(f"- stop/trigger-like orders observed: {len(stop_like_orders)}")
    unprotected_positions = _find_unprotected_positions(report)
    lines.append(f"- unprotected open positions: {len(unprotected_positions)}")
    if unprotected_positions:
        lines.append("- unprotected positions:")
        lines.extend(
            f"  {position.symbol} {position.side} qty={position.contracts:g} "
            f"notional={position.notional_usdt:.2f}"
            for position in unprotected_positions
        )

    if small_orders:
        smallest = sorted(small_orders, key=lambda item: item.notional_usdt)[:10]
        lines.append("- smallest filled orders:")
        lines.extend(
            f"  {order.symbol} {order.side} notional={order.notional_usdt:.2f} "
            f"filled={order.filled:g} avg={order.average_price:g} id={order.client_order_id or order.order_id}"
            for order in smallest
        )

    if round_trips:
        lines.append("- shortest inferred round trips:")
        for item in sorted(round_trips, key=lambda row: row["hold_minutes"])[:10]:
            lines.append(
                f"  {item['symbol']} {item['entry_side']}->{item['exit_side']} "
                f"hold={item['hold_minutes']:.1f}m "
                f"entry={item['entry_price']:g} exit={item['exit_price']:g} "
                f"notional={item['exit_notional_usdt']:.2f}"
            )

    return lines


def _find_unprotected_positions(report: AnalysisReport) -> list[PositionSummary]:
    protected_symbols = {
        _symbol_key(order.symbol)
        for order in report.open_orders
        if _is_protective_stop_order(order)
    }
    return [
        position
        for position in report.positions
        if _symbol_key(position.symbol) not in protected_symbols
    ]


def _is_protective_stop_order(order: OrderSummary) -> bool:
    return bool(
        order.reduce_only
        and (
            order.stop_trigger_price
            or order.raw_stop_order_type
            or "stop" in order.order_type.lower()
            or "stop" in order.raw_order_type.lower()
        )
    )


def _infer_round_trips(orders: list[OrderSummary]) -> list[dict[str, Any]]:
    entries_by_symbol: dict[str, list[OrderSummary]] = defaultdict(list)
    round_trips: list[dict[str, Any]] = []
    for order in sorted(orders, key=lambda item: item.timestamp or 0):
        if order.filled <= 0 or order.timestamp is None:
            continue
        if not order.reduce_only:
            entries_by_symbol[order.symbol].append(order)
            continue

        entry = _pop_matching_entry(entries_by_symbol[order.symbol], order)
        if entry is None or entry.timestamp is None:
            continue
        hold_minutes = (order.timestamp - entry.timestamp) / 60000
        if hold_minutes < 0:
            continue
        round_trips.append(
            {
                "symbol": order.symbol,
                "entry_side": entry.side,
                "exit_side": order.side,
                "entry_price": entry.average_price,
                "exit_price": order.average_price,
                "entry_notional_usdt": entry.notional_usdt,
                "exit_notional_usdt": order.notional_usdt,
                "hold_minutes": hold_minutes,
                "entry_order_id": entry.client_order_id or entry.order_id,
                "exit_order_id": order.client_order_id or order.order_id,
            }
        )
    return round_trips


def _pop_matching_entry(
    entries: list[OrderSummary],
    exit_order: OrderSummary,
) -> OrderSummary | None:
    opposite_side = "buy" if exit_order.side.lower() == "sell" else "sell"
    for index, entry in enumerate(entries):
        if entry.side.lower() == opposite_side:
            return entries.pop(index)
    return entries.pop(0) if entries else None


def _format_time(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "unknown-time"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp_ms / 1000))


def _bybit_market_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace(":USDT", "")


def _symbol_key(symbol: str) -> str:
    return _bybit_market_symbol(symbol).upper()


def _float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    args = build_parser().parse_args()
    load_runtime_env(args.env)
    exchange = build_exchange(account_mode=args.account_mode)
    report = analyze_exchange(
        exchange,
        since_hours=args.since_hours,
        limit=args.limit,
        symbols=args.symbol,
    )
    if args.json:
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
    else:
        print(render_report(report))
    if args.fail_unprotected_positions and _find_unprotected_positions(report):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
