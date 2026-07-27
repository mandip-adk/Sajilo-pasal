from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from orders.models import (
    Order, OrderStatus, OrderTransitionError,
    PaymentStatus, PaymentMethod, OrderPaymentError,
)
from shops.models import Shop

ORDERS_PER_PAGE = 20

# Day 14 — UI presentation of Order.ALLOWED_TRANSITIONS. Kept separate
# from the model's state machine on purpose: this is "what button do
# we show and what does it say", not "what's a legal transition" —
# that validation lives in Order.transition_status() itself and is
# re-checked there regardless of what this dict offers.
NEXT_STATUS_ACTIONS = {
    OrderStatus.PENDING: [
        (OrderStatus.PREPARING, "Start Preparing", "btn-primary"),
        (OrderStatus.CANCELLED, "Cancel Order", "btn-outline-danger"),
    ],
    OrderStatus.PREPARING: [
        (OrderStatus.READY, "Mark Ready", "btn-success"),
        (OrderStatus.CANCELLED, "Cancel Order", "btn-outline-danger"),
    ],
    OrderStatus.READY: [],
    OrderStatus.CANCELLED: [],
}


@login_required
def dashboard_home_view(request):
    """
    Landing page after login — a shop switcher.

    Shop.owner is a plain FK (not OneToOne), so one account can run
    more than one shop (e.g. a family running both a tea shop and a
    kirana store). This lists every shop the logged-in user owns, with
    a pending-order count per shop, linking into that shop's order
    dashboard.
    """
    shops = (
        Shop.objects
        .filter(owner=request.user)
        .order_by("name")
    )

    # Pending count per shop in one query, rather than one COUNT query
    # per shop card if the template called shop.orders.filter(...)
    # directly — matters once an owner has several shops.
    pending_counts = dict(
        Order.objects
        .filter(shop__owner=request.user, status=OrderStatus.PENDING)
        .values("shop_id")
        .annotate(count=Count("id"))
        .values_list("shop_id", "count")
    )
    for shop in shops:
        shop.pending_count = pending_counts.get(shop.id, 0)

    return render(request, "dashboard/home.html", {"shops": shops})


@login_required
def shop_orders_view(request, shop_slug):
    """
    Order list for a single shop.

    Ownership check is a single joined queryset (shop__owner via the
    get_object_or_404 below) — same defensive pattern used for
    products/categories, so a request for another owner's shop 404s
    instead of confirming the shop exists.

    Filterable via ?status=pending|preparing|ready|cancelled. This
    view is read-only — no status-change controls here, that's Day 14.
    """
    shop = get_object_or_404(Shop, slug=shop_slug, owner=request.user)

    status_filter = request.GET.get("status", "")
    orders_qs = (
        Order.objects
        .filter(shop=shop)
        .annotate(item_count=Count("items"))  # avoids per-row .items.count() N+1
        .order_by("-created_at")
    )
    if status_filter in OrderStatus.values:
        orders_qs = orders_qs.filter(status=status_filter)

    # Per-status counts for the filter tabs, one query rather than one
    # per tab.
    status_counts = dict(
        Order.objects
        .filter(shop=shop)
        .values("status")
        .annotate(count=Count("id"))
        .values_list("status", "count")
    )
    status_tabs = [
        {"value": value, "label": label, "count": status_counts.get(value, 0)}
        for value, label in OrderStatus.choices
    ]

    paginator = Paginator(orders_qs, ORDERS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "dashboard/shop_orders.html", {
        "shop": shop,
        "page_obj": page_obj,
        "status_filter": status_filter,
        "status_tabs": status_tabs,
        "total_orders": Order.objects.filter(shop=shop).count(),
    })


@login_required
def order_detail_view(request, shop_slug, order_id):
    """
    Single order detail — line items, table number, customer note.

    Three-hop-style ownership check (order id + shop slug + shop
    owner), all in one joined queryset — same defensive pattern as
    Product's three-hop ownership check.
    """
    order = get_object_or_404(
        Order.objects.select_related("shop").prefetch_related("items"),
        pk=order_id,
        shop__slug=shop_slug,
        shop__owner=request.user,
    )
    return render(request, "dashboard/order_detail.html", {
        "shop": order.shop,
        "order": order,
        "next_actions": NEXT_STATUS_ACTIONS.get(order.status, []),
    })


@login_required
@require_POST
def update_order_status_view(request, shop_slug, order_id):
    """
    Day 14 — the owner's status-change action from the order detail
    page. Same three-hop ownership check as order_detail_view, so this
    can't be used to mutate an order that isn't the logged-in owner's.

    Delegates all transition validity + stock adjustment to
    Order.transition_status() — this view is just the HTTP wrapper:
    read the requested status, call the model method, translate the
    result (or OrderTransitionError) into a message, redirect back.
    """
    order = get_object_or_404(
        Order,
        pk=order_id,
        shop__slug=shop_slug,
        shop__owner=request.user,
    )

    new_status = request.POST.get("new_status", "")
    if new_status not in OrderStatus.values:
        messages.error(request, "That's not a valid order status.")
        return redirect("dashboard:order_detail", shop_slug=shop_slug, order_id=order_id)

    try:
        order.transition_status(new_status)
    except OrderTransitionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Order {order.order_number_display} marked {order.get_status_display()}.",
        )

    return redirect("dashboard:order_detail", shop_slug=shop_slug, order_id=order_id)


@login_required
@require_POST
def update_order_payment_view(request, shop_slug, order_id):
    """
    Day 15 — payment status / method tracking from the order detail
    page. Same three-hop ownership check as the other order-mutating
    views.

    The dashboard submits payment_status and payment_method as two
    separate forms (a "Mark Paid"/"Mark Unpaid" toggle, and a method
    correction dropdown), so a single request here only ever carries
    one of the two POST keys in practice — but both are read and
    passed through if present, since Order.update_payment() supports
    updating either or both in one call.
    """
    order = get_object_or_404(
        Order,
        pk=order_id,
        shop__slug=shop_slug,
        shop__owner=request.user,
    )

    payment_status = request.POST.get("payment_status") or None
    payment_method = request.POST.get("payment_method") or None

    if payment_status is None and payment_method is None:
        messages.error(request, "No payment change was submitted.")
        return redirect("dashboard:order_detail", shop_slug=shop_slug, order_id=order_id)

    if payment_status is not None and payment_status not in PaymentStatus.values:
        messages.error(request, "That's not a valid payment status.")
        return redirect("dashboard:order_detail", shop_slug=shop_slug, order_id=order_id)

    if payment_method is not None and payment_method not in PaymentMethod.values:
        messages.error(request, "That's not a valid payment method.")
        return redirect("dashboard:order_detail", shop_slug=shop_slug, order_id=order_id)

    try:
        order.update_payment(payment_status=payment_status, payment_method=payment_method)
    except OrderPaymentError as exc:
        messages.error(request, str(exc))
    else:
        parts = []
        if payment_status is not None:
            parts.append(order.get_payment_status_display())
        if payment_method is not None:
            parts.append(order.get_payment_method_display())
        messages.success(
            request,
            f"Order {order.order_number_display} updated: {', '.join(parts)}.",
        )

    return redirect("dashboard:order_detail", shop_slug=shop_slug, order_id=order_id)

