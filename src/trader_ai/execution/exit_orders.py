from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from typing import Callable

from nautilus_trader.model.enums import OrderSide

from trader_ai.execution.instrumentation import execution_instrumentation


EXIT_STOP_TAG = "EXIT_STOP"
EXIT_TAKE_PROFIT_TAG = "EXIT_TAKE_PROFIT"


class ExitOrderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExitOrderRequest:
    instrument: Any
    instrument_id: Any
    position_side: OrderSide
    quantity: Any
    entry_price: Decimal
    stop_price: Decimal
    strategy_name: str
    symbol: str
    trade_id: str
    take_profit_order_enabled: bool = False
    take_profit_r_multiple: Decimal = Decimal("2.0")
    profit_runner_enabled: bool = True
    profit_runner_activation_r_multiple: Decimal = Decimal("2.0")
    profit_runner_stop_lock_r_multiple: Decimal = Decimal("0.5")


@dataclass(frozen=True)
class ExitOrderHandles:
    request: ExitOrderRequest
    stop_order: Any | None
    take_profit_order: Any | None
    stop_price: Decimal
    take_profit_price: Decimal | None
    profit_runner_active: bool = False


def exit_order_side(position_side: OrderSide) -> OrderSide:
    if position_side == OrderSide.BUY:
        return OrderSide.SELL
    if position_side == OrderSide.SELL:
        return OrderSide.BUY
    raise ExitOrderError(f"Unsupported position side for exit order: {position_side}")


def calculate_take_profit_price(
    *,
    position_side: OrderSide,
    entry_price: Decimal,
    stop_price: Decimal,
    r_multiple: Decimal,
) -> Decimal:
    risk = abs(entry_price - stop_price)
    if risk <= 0:
        raise ExitOrderError(
            f"Take-profit risk must be positive: entry={entry_price} stop={stop_price}"
        )
    if r_multiple <= 0:
        raise ExitOrderError(f"Take-profit R multiple must be positive: {r_multiple}")
    if position_side == OrderSide.BUY:
        return entry_price + (r_multiple * risk)
    if position_side == OrderSide.SELL:
        return entry_price - (r_multiple * risk)
    raise ExitOrderError(f"Unsupported position side for take-profit: {position_side}")


def calculate_profit_runner_activation_price(request: ExitOrderRequest) -> Decimal:
    risk = _position_risk(request)
    if request.position_side == OrderSide.BUY:
        return request.entry_price + (request.profit_runner_activation_r_multiple * risk)
    if request.position_side == OrderSide.SELL:
        return request.entry_price - (request.profit_runner_activation_r_multiple * risk)
    raise ExitOrderError(f"Unsupported position side for profit runner: {request.position_side}")


def calculate_profit_runner_stop_price(request: ExitOrderRequest) -> Decimal:
    risk = _position_risk(request)
    if request.position_side == OrderSide.BUY:
        return request.entry_price + (request.profit_runner_stop_lock_r_multiple * risk)
    if request.position_side == OrderSide.SELL:
        return request.entry_price - (request.profit_runner_stop_lock_r_multiple * risk)
    raise ExitOrderError(f"Unsupported position side for profit runner: {request.position_side}")


def record_exit_order_request(
    *,
    owner: Any,
    entry_order: Any,
    request: ExitOrderRequest,
) -> None:
    requests = getattr(owner, "_exit_order_requests", {})
    requests[request.trade_id] = request
    client_order_id = getattr(entry_order, "client_order_id", None)
    if client_order_id is not None:
        requests[str(client_order_id)] = request
    owner._exit_order_requests = requests


def submit_pending_exit_orders(*, owner: Any, trade_id: str) -> ExitOrderHandles | None:
    requests = getattr(owner, "_exit_order_requests", {})
    request = requests.pop(trade_id, None)
    if request is None:
        return None

    for key, pending_request in list(requests.items()):
        if pending_request.trade_id == request.trade_id:
            requests.pop(key, None)
    owner._exit_order_requests = requests

    handle_exit_order_failure = getattr(owner, "_handle_exit_order_failure", None)
    return submit_exit_orders_after_entry(
        owner=owner,
        request=request,
        on_failure=(
            handle_exit_order_failure
            if callable(handle_exit_order_failure)
            else lambda: owner.close_all_positions(request.instrument_id)
        ),
    )


def submit_exit_orders_after_entry(
    *,
    owner: Any,
    request: ExitOrderRequest,
    on_failure: Callable[[], None] | None = None,
) -> ExitOrderHandles | None:
    try:
        return ensure_exit_orders(owner=owner, request=request)
    except Exception as exc:
        execution_instrumentation.exit_order_failed(
            strategy=request.strategy_name,
            symbol=request.symbol,
            side=exit_order_side(request.position_side).name,
            trade_id=request.trade_id,
            order_role="exit_orders",
            reason=exc.__class__.__name__,
            error=str(exc),
            result="error",
            instrument_id=str(request.instrument_id),
            stop_price=float(request.stop_price),
            entry_price=float(request.entry_price),
        )
        execution_instrumentation.runtime_error(
            component="exit_orders",
            reason=exc.__class__.__name__,
            error=str(exc),
            strategy=request.strategy_name,
            symbol=request.symbol,
            trade_id=request.trade_id,
            instrument_id=str(request.instrument_id),
            stop_price=float(request.stop_price),
            entry_price=float(request.entry_price),
        )
        if on_failure is not None:
            on_failure()
        return None


def ensure_exit_orders(*, owner: Any, request: ExitOrderRequest) -> ExitOrderHandles:
    _validate_exit_stop(request)
    stop_order = _find_existing_exit_order(
        owner=owner,
        instrument_id=request.instrument_id,
        trade_id=request.trade_id,
        tag=EXIT_STOP_TAG,
        order_types={"STOP_MARKET", "TRAILING_STOP_MARKET"},
    )
    take_profit_price = None
    take_profit_order = None
    if request.take_profit_order_enabled:
        take_profit_price = calculate_take_profit_price(
            position_side=request.position_side,
            entry_price=request.entry_price,
            stop_price=request.stop_price,
            r_multiple=request.take_profit_r_multiple,
        )
        take_profit_order = _find_existing_exit_order(
            owner=owner,
            instrument_id=request.instrument_id,
            trade_id=request.trade_id,
            tag=EXIT_TAKE_PROFIT_TAG,
            order_types={"LIMIT"},
        )

    side = exit_order_side(request.position_side)
    if stop_order is None:
        stop_order = owner.order_factory.stop_market(
            request.instrument_id,
            side,
            request.quantity,
            request.instrument.make_price(request.stop_price),
            reduce_only=True,
            tags=_exit_order_tags(
                tag=EXIT_STOP_TAG,
                strategy_name=request.strategy_name,
                symbol=request.symbol,
                trade_id=request.trade_id,
            ),
        )
        owner.submit_order(stop_order)
        execution_instrumentation.exit_order_submitted(
            strategy=request.strategy_name,
            symbol=request.symbol,
            side=side.name,
            trade_id=request.trade_id,
            order_id=str(stop_order.client_order_id),
            order_role="stop",
            quantity=_quantity_as_float(request.quantity),
            price=float(request.stop_price),
            result="submitted",
        )

    if request.take_profit_order_enabled and take_profit_order is None:
        take_profit_order = owner.order_factory.limit(
            request.instrument_id,
            side,
            request.quantity,
            request.instrument.make_price(take_profit_price),
            post_only=False,
            reduce_only=True,
            tags=_exit_order_tags(
                tag=EXIT_TAKE_PROFIT_TAG,
                strategy_name=request.strategy_name,
                symbol=request.symbol,
                trade_id=request.trade_id,
            ),
        )
        owner.submit_order(take_profit_order)
        execution_instrumentation.exit_order_submitted(
            strategy=request.strategy_name,
            symbol=request.symbol,
            side=side.name,
            trade_id=request.trade_id,
            order_id=str(take_profit_order.client_order_id),
            order_role="take_profit",
            quantity=_quantity_as_float(request.quantity),
            price=float(take_profit_price),
            result="submitted",
        )

    handles = ExitOrderHandles(
        request=request,
        stop_order=stop_order,
        take_profit_order=take_profit_order,
        stop_price=request.stop_price,
        take_profit_price=take_profit_price,
    )
    _store_exit_order_handles(owner=owner, handles=handles)
    return handles


def replace_exit_stop_if_improved(
    *,
    owner: Any,
    trade_id: str,
    stop_price: Decimal,
    min_improvement_bps: int,
) -> bool:
    handles = getattr(owner, "_exit_orders_by_trade_id", {}).get(trade_id)
    if handles is None or handles.stop_order is None:
        return False

    current_stop = handles.stop_price
    request = handles.request
    if not _is_stop_improved(
        position_side=request.position_side,
        current_stop=current_stop,
        new_stop=stop_price,
        min_improvement_bps=min_improvement_bps,
    ):
        return False

    new_stop_order = owner.order_factory.stop_market(
        request.instrument_id,
        exit_order_side(request.position_side),
        request.quantity,
        request.instrument.make_price(stop_price),
        reduce_only=True,
        tags=_exit_order_tags(
            tag=EXIT_STOP_TAG,
            strategy_name=request.strategy_name,
            symbol=request.symbol,
            trade_id=request.trade_id,
        ),
    )
    owner.submit_order(new_stop_order)
    owner.cancel_order(handles.stop_order)
    updated = ExitOrderHandles(
        request=request,
        stop_order=new_stop_order,
        take_profit_order=handles.take_profit_order,
        stop_price=stop_price,
        take_profit_price=handles.take_profit_price,
        profit_runner_active=handles.profit_runner_active,
    )
    _remove_exit_order_indexes(owner=owner, trade_id=trade_id)
    _store_exit_order_handles(owner=owner, handles=updated)
    execution_instrumentation.exit_stop_replaced(
        strategy=request.strategy_name,
        symbol=request.symbol,
        side=exit_order_side(request.position_side).name,
        trade_id=request.trade_id,
        old_order_id=str(handles.stop_order.client_order_id),
        new_order_id=str(new_stop_order.client_order_id),
        quantity=_quantity_as_float(request.quantity),
        previous_stop_price=float(current_stop),
        new_stop_price=float(stop_price),
        result="replaced",
    )
    return True


def activate_profit_runner_if_ready(
    *,
    owner: Any,
    trade_id: str,
    latest_price: Decimal,
    min_improvement_bps: int,
) -> bool:
    handles = getattr(owner, "_exit_orders_by_trade_id", {}).get(trade_id)
    if handles is None or handles.profit_runner_active:
        return False

    request = handles.request
    if not request.profit_runner_enabled:
        return False
    if not _has_crossed_profit_runner_activation(request, latest_price):
        return False

    locked_stop = calculate_profit_runner_stop_price(request)
    replaced = replace_exit_stop_if_improved(
        owner=owner,
        trade_id=trade_id,
        stop_price=locked_stop,
        min_improvement_bps=min_improvement_bps,
    )
    current_handles = getattr(owner, "_exit_orders_by_trade_id", {}).get(trade_id)
    if current_handles is None:
        return replaced

    if replaced or _stop_already_locks_profit(
        position_side=request.position_side,
        current_stop=current_handles.stop_price,
        locked_stop=locked_stop,
    ):
        updated = ExitOrderHandles(
            request=current_handles.request,
            stop_order=current_handles.stop_order,
            take_profit_order=current_handles.take_profit_order,
            stop_price=current_handles.stop_price,
            take_profit_price=current_handles.take_profit_price,
            profit_runner_active=True,
        )
        _store_exit_order_handles(owner=owner, handles=updated)
        execution_instrumentation.profit_runner_activated(
            strategy=request.strategy_name,
            symbol=request.symbol,
            side=request.position_side.name,
            trade_id=request.trade_id,
            latest_price=float(latest_price),
            activation_price=float(calculate_profit_runner_activation_price(request)),
            locked_stop_price=float(locked_stop),
            result="activated",
        )
        return True
    return False


def cancel_sibling_exit_order(*, owner: Any, filled_order_id: str) -> bool:
    trade_id = getattr(owner, "_exit_order_trade_ids", {}).get(filled_order_id)
    if trade_id is None:
        return False

    role = getattr(owner, "_exit_order_roles", {}).get(filled_order_id)
    handles = getattr(owner, "_exit_orders_by_trade_id", {}).get(trade_id)
    if handles is None:
        return False

    sibling = handles.take_profit_order if role == "stop" else handles.stop_order
    if sibling is None:
        return False
    owner.cancel_order(sibling)
    execution_instrumentation.exit_order_canceled(
        strategy=handles.request.strategy_name,
        symbol=handles.request.symbol,
        side=exit_order_side(handles.request.position_side).name,
        trade_id=trade_id,
        order_id=str(sibling.client_order_id),
        order_role="take_profit" if role == "stop" else "stop",
        reason="sibling_filled",
        result="canceled",
    )
    getattr(owner, "_exit_orders_by_trade_id", {}).pop(trade_id, None)
    _remove_exit_order_indexes(owner=owner, trade_id=trade_id)
    return True


def cancel_exit_orders(*, owner: Any, trade_id: str) -> bool:
    handles = getattr(owner, "_exit_orders_by_trade_id", {}).pop(trade_id, None)
    if handles is None:
        return False

    cancelled = False
    for role, order in (
        ("stop", handles.stop_order),
        ("take_profit", handles.take_profit_order),
    ):
        if order is None:
            continue
        owner.cancel_order(order)
        execution_instrumentation.exit_order_canceled(
            strategy=handles.request.strategy_name,
            symbol=handles.request.symbol,
            side=exit_order_side(handles.request.position_side).name,
            trade_id=trade_id,
            order_id=str(order.client_order_id),
            order_role=role,
            reason="trade_exit_cleanup",
            result="canceled",
        )
        cancelled = True
    _remove_exit_order_indexes(owner=owner, trade_id=trade_id)
    return cancelled


def _validate_exit_stop(request: ExitOrderRequest) -> None:
    if request.stop_price <= 0:
        raise ExitOrderError(f"Exit stop price must be positive: {request.stop_price}")
    if request.position_side == OrderSide.BUY and request.stop_price >= request.entry_price:
        raise ExitOrderError(
            f"Long exit stop must be below entry: stop={request.stop_price} entry={request.entry_price}"
        )
    if request.position_side == OrderSide.SELL and request.stop_price <= request.entry_price:
        raise ExitOrderError(
            f"Short exit stop must be above entry: stop={request.stop_price} entry={request.entry_price}"
        )
    if request.position_side not in (OrderSide.BUY, OrderSide.SELL):
        raise ExitOrderError(f"Unsupported position side for exit stop: {request.position_side}")
    _position_risk(request)


def _find_existing_exit_order(
    *,
    owner: Any,
    instrument_id: Any,
    trade_id: str,
    tag: str,
    order_types: set[str],
) -> Any | None:
    cache = getattr(owner, "cache", None)
    if cache is None or not callable(getattr(cache, "orders_open", None)):
        return None

    for order in cache.orders_open(instrument_id=instrument_id):
        if not _order_is_reduce_only(order):
            continue
        order_type = getattr(getattr(order, "order_type", None), "name", "")
        if order_type not in order_types:
            continue
        tags = set(getattr(order, "tags", None) or [])
        if tag in tags and f"trade_id={trade_id}" in tags:
            return order
    return None


def _order_is_reduce_only(order: Any) -> bool:
    for attr_name in ("reduce_only", "is_reduce_only", "would_reduce_only"):
        value = getattr(order, attr_name, None)
        if value is None:
            continue
        return bool(value() if callable(value) else value)
    return False


def _is_stop_improved(
    *,
    position_side: OrderSide,
    current_stop: Decimal,
    new_stop: Decimal,
    min_improvement_bps: int,
) -> bool:
    min_improvement = current_stop * Decimal(str(min_improvement_bps)) / Decimal("10000")
    if position_side == OrderSide.BUY:
        return new_stop - current_stop >= min_improvement
    if position_side == OrderSide.SELL:
        return current_stop - new_stop >= min_improvement
    return False


def _position_risk(request: ExitOrderRequest) -> Decimal:
    risk = abs(request.entry_price - request.stop_price)
    if risk <= 0:
        raise ExitOrderError(
            f"Position risk must be positive: entry={request.entry_price} stop={request.stop_price}"
        )
    if request.profit_runner_activation_r_multiple <= 0:
        raise ExitOrderError(
            f"Profit runner activation R multiple must be positive: {request.profit_runner_activation_r_multiple}"
        )
    if request.profit_runner_stop_lock_r_multiple < 0:
        raise ExitOrderError(
            f"Profit runner stop lock R multiple cannot be negative: {request.profit_runner_stop_lock_r_multiple}"
        )
    return risk


def _has_crossed_profit_runner_activation(
    request: ExitOrderRequest,
    latest_price: Decimal,
) -> bool:
    activation_price = calculate_profit_runner_activation_price(request)
    if request.position_side == OrderSide.BUY:
        return latest_price >= activation_price
    if request.position_side == OrderSide.SELL:
        return latest_price <= activation_price
    return False


def _stop_already_locks_profit(
    *,
    position_side: OrderSide,
    current_stop: Decimal,
    locked_stop: Decimal,
) -> bool:
    if position_side == OrderSide.BUY:
        return current_stop >= locked_stop
    if position_side == OrderSide.SELL:
        return current_stop <= locked_stop
    return False


def _exit_order_tags(
    *,
    tag: str,
    strategy_name: str,
    symbol: str,
    trade_id: str,
) -> list[str]:
    return [
        tag,
        f"strategy={strategy_name}",
        f"symbol={symbol}",
        f"trade_id={trade_id}",
    ]


def _store_exit_order_handles(*, owner: Any, handles: ExitOrderHandles) -> None:
    by_trade_id = getattr(owner, "_exit_orders_by_trade_id", {})
    trade_ids = getattr(owner, "_exit_order_trade_ids", {})
    roles = getattr(owner, "_exit_order_roles", {})
    by_trade_id[handles.request.trade_id] = handles
    for role, order in (
        ("stop", handles.stop_order),
        ("take_profit", handles.take_profit_order),
    ):
        if order is None:
            continue
        order_id = str(order.client_order_id)
        trade_ids[order_id] = handles.request.trade_id
        roles[order_id] = role
    owner._exit_orders_by_trade_id = by_trade_id
    owner._exit_order_trade_ids = trade_ids
    owner._exit_order_roles = roles


def _remove_exit_order_indexes(*, owner: Any, trade_id: str) -> None:
    trade_ids = getattr(owner, "_exit_order_trade_ids", {})
    roles = getattr(owner, "_exit_order_roles", {})
    for order_id, mapped_trade_id in list(trade_ids.items()):
        if mapped_trade_id != trade_id:
            continue
        trade_ids.pop(order_id, None)
        roles.pop(order_id, None)
    owner._exit_order_trade_ids = trade_ids
    owner._exit_order_roles = roles


def _quantity_as_float(quantity: Any) -> float:
    try:
        return float(str(quantity))
    except ValueError:
        return 0.0
