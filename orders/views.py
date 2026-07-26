import json

from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from products.models import Product
from shops.models import Shop

from .models import Order, PaymentMethod

MAX_TABLE_NUMBER_LENGTH = 50
MAX_NOTE_LENGTH = 500
MAX_ITEM_QUANTITY = 99  # sanity cap against a malformed/huge payload


def _error(message, code="invalid_request", status=400, **extra):
    payload = {"success": False, "error": code, "message": message}
    payload.update(extra)
    return JsonResponse(payload, status=status)


def _serialize_order(order):
    return {
        "order_number": order.order_number,
        "order_number_display": order.order_number_display,
        "token": str(order.order_token),
        "status": order.status,
        "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "table_number": order.table_number,
        "customer_note": order.customer_note,
        "subtotal": str(order.subtotal),
        "created_at": order.created_at.isoformat(),
        "items": [
            {
                "name": item.product_name,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
                "line_total": str(item.line_total),
            }
            for item in order.items.all()
        ],
    }


@require_POST
@csrf_protect
def place_order_view(request, shop_slug):
    """
    Day 12 — order placement.

    Accepts a JSON POST from the cart drawer (fetch, no page reload —
    see cart.js submitOrder()). The client-side cart (localStorage) is
    treated as untrusted input: only product id + quantity are taken
    from it, price and name are always re-read from the DB.

    Idempotent on order_token: if the same token was already used to
    place an order for this shop (customer double-tapped "Place Order"
    on a slow connection, or a fetch retried after a dropped response),
    the existing order is returned instead of creating a duplicate.

    Does NOT touch stock — create_from_cart() deliberately leaves that
    for the Day 14 status-change-to-Preparing flow.
    """
    shop = get_object_or_404(Shop, slug=shop_slug, is_active=True)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error("Could not read your order. Please try again.", "bad_json")

    if not isinstance(payload, dict):
        return _error("Could not read your order. Please try again.", "bad_json")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return _error("Your cart is empty.", "cart_empty")

    # ── Idempotency check ──
    order_token = payload.get("order_token") or None
    if order_token:
        existing = Order.objects.filter(shop=shop, order_token=order_token).first()
        if existing:
            return JsonResponse(
                {"success": True, "duplicate": True, "order": _serialize_order(existing)},
                status=200,
            )

    table_number = str(payload.get("table_number", "") or "").strip()[:MAX_TABLE_NUMBER_LENGTH]
    customer_note = str(payload.get("customer_note", "") or "").strip()[:MAX_NOTE_LENGTH]

    payment_method = payload.get("payment_method") or PaymentMethod.CASH
    if payment_method not in PaymentMethod.values:
        return _error("Please choose a valid payment method.", "invalid_payment_method")

    # ── Validate + re-price every line item server-side ──
    cleaned_items = []
    unavailable = []

    for raw in raw_items:
        if not isinstance(raw, dict):
            return _error("Your cart looks corrupted. Please refresh and try again.", "bad_item")

        try:
            product_id = int(raw.get("id"))
            quantity = int(raw.get("quantity"))
        except (TypeError, ValueError):
            return _error("Your cart looks corrupted. Please refresh and try again.", "bad_item")

        if quantity < 1 or quantity > MAX_ITEM_QUANTITY:
            return _error("Please check the quantities in your cart.", "bad_quantity")

        # Scoped to this shop via the category FK — a product id from
        # another shop's menu, or a stale/tampered id, simply isn't
        # found here rather than being confirmed to exist elsewhere.
        product = (
            Product.objects
            .select_related("category")
            .filter(pk=product_id, category__shop=shop)
            .first()
        )

        if product is None:
            unavailable.append({"id": product_id, "name": raw.get("name", "Item"), "reason": "not_found"})
            continue

        if not product.is_orderable:
            reason = "unavailable" if not product.is_available else "out_of_stock"
            unavailable.append({"id": product_id, "name": product.name, "reason": reason})
            continue

        if not product.allow_over_order and quantity > product.stock_quantity:
            unavailable.append({
                "id": product_id,
                "name": product.name,
                "reason": "insufficient_stock",
                "available": product.stock_quantity,
            })
            continue

        cleaned_items.append({
            "id": product.pk,
            "name": product.name,
            "price": product.price,   # server price — client price is ignored
            "quantity": quantity,
        })

    if unavailable:
        return _error(
            "Some items in your cart are no longer available. Please review your cart.",
            "items_unavailable",
            unavailable_items=unavailable,
        )

    if not cleaned_items:
        return _error("Your cart is empty.", "cart_empty")

    try:
        order = Order.create_from_cart(
            shop=shop,
            cart_items=cleaned_items,
            table_number=table_number,
            customer_note=customer_note,
            payment_method=payment_method,
            order_token=order_token,
        )
    except IntegrityError:
        # Extremely rare race survives select_for_update (e.g. two
        # requests reusing the same token colliding) — safe to ask the
        # customer to retry once.
        return _error("Could not place your order, please try again.", "order_conflict", status=409)

    return JsonResponse(
        {"success": True, "duplicate": False, "order": _serialize_order(order)},
        status=201,
    )
