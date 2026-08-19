import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from orders.models import (
    Order, OrderStatus, OrderTransitionError,
    PaymentStatus, PaymentMethod, OrderPaymentError,
)
from products.models import Product
from shops.models import Shop


ORDERS_PER_PAGE = 20

LOW_STOCK_FILTER = dict(
    allow_over_order=False,
    stock_quantity__gt=0,
    stock_quantity__lte=5,
)

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

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]



@login_required
def dashboard_home_view(request):
    """
    Landing page after login — shop switcher + at-a-glance stats.

    Everything here is aggregated across every shop the user owns
    (owner-scoped, same defensive filter used throughout this file),
    not scoped to one shop, since there's no single "current shop"
    on this page.
    """
    shops = (
        Shop.objects
        .filter(owner=request.user)
        .order_by("name")
    )

    owner_orders = Order.objects.filter(shop__owner=request.user)

    today = timezone.localdate()

    # ── Today's orders / revenue ──
    todays_orders_qs = owner_orders.filter(created_at__date=today)
    todays_orders_count = todays_orders_qs.count()
    todays_revenue = todays_orders_qs.aggregate(total=Sum("subtotal"))["total"] or 0

    # Yesterday, for the "+N from yesterday" / "+RsX (Y%)" deltas.
    yesterday = today - datetime.timedelta(days=1)
    yesterdays_orders_qs = owner_orders.filter(created_at__date=yesterday)
    yesterdays_orders_count = yesterdays_orders_qs.count()
    yesterdays_revenue = yesterdays_orders_qs.aggregate(total=Sum("subtotal"))["total"] or 0

    orders_delta = todays_orders_count - yesterdays_orders_count
    revenue_delta = todays_revenue - yesterdays_revenue
    revenue_delta_pct = (
        round((revenue_delta / yesterdays_revenue) * 100, 1)
        if yesterdays_revenue else None
    )

    # ── Preparing orders (across all shops) ──
    preparing_count = owner_orders.filter(status=OrderStatus.PREPARING).count()

    # ── Recent orders (latest 3 across all shops) ──
    recent_orders = (
        owner_orders
        .select_related("shop")
        .annotate(item_count=Count("items"))
        .order_by("-created_at")[:3]
    )

    # ── Revenue overview: current week (Mon–Sun) ──
    start_of_week = today - datetime.timedelta(days=today.weekday())
    end_of_week = start_of_week + datetime.timedelta(days=6)
    start_of_last_week = start_of_week - datetime.timedelta(days=7)
    end_of_last_week = start_of_week - datetime.timedelta(days=1)

    this_week_by_day = dict(
        owner_orders
        .filter(created_at__date__range=(start_of_week, end_of_week))
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Sum("subtotal"))
        .values_list("day", "total")
    )
    chart_values = [
        float(this_week_by_day.get(start_of_week + datetime.timedelta(days=i), 0) or 0)
        for i in range(7)
    ]

    this_week_total = sum(chart_values)
    last_week_total = float(
        owner_orders
        .filter(created_at__date__range=(start_of_last_week, end_of_last_week))
        .aggregate(total=Sum("subtotal"))["total"] or 0
    )
    week_over_week_pct = (
        round(((this_week_total - last_week_total) / last_week_total) * 100, 1)
        if last_week_total else None
    )

    return render(request, "dashboard/home.html", {
        "shops": shops,
        "todays_orders_count": todays_orders_count,
        "todays_revenue": todays_revenue,
        "orders_delta": orders_delta,
        "revenue_delta": revenue_delta,
        "revenue_delta_pct": revenue_delta_pct,
        "preparing_count": preparing_count,
        "recent_orders": recent_orders,
        "chart_labels": WEEKDAY_LABELS,
        "chart_values": chart_values,
        "this_week_total": this_week_total,
        "last_week_total": last_week_total,
        "week_over_week_pct": week_over_week_pct,
    })


@login_required
def shop_orders_view(request, shop_slug):
    """
    Order list for a single shop.
    ...(docstring unchanged)...
    """
    shop = get_object_or_404(Shop, slug=shop_slug, owner=request.user)

    status_filter = request.GET.get("status", "")
    orders_qs = (
        Order.objects
        .filter(shop=shop)
        .annotate(item_count=Count("items"))
        .order_by("-created_at")
    )
    if status_filter in OrderStatus.values:
        orders_qs = orders_qs.filter(status=status_filter)

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

    # Day 21 — sidebar / bottom-nav "Orders" badge count. Sum of the
    # three actionable states; deliberately excludes READY/CANCELLED-
    # adjacent double counting logic, just the three open buckets.
    open_orders_count = sum(
        status_counts.get(status, 0)
        for status in (OrderStatus.PENDING, OrderStatus.PREPARING, OrderStatus.READY)
    )

    paginator = Paginator(orders_qs, ORDERS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))

    low_stock_count = Product.objects.filter(category__shop=shop, **LOW_STOCK_FILTER).count()

    latest_seen_id = Order.objects.filter(shop=shop).aggregate(
        Max("id")
    )["id__max"] or 0

    return render(request, "dashboard/shop_orders.html", {
        "shop": shop,
        "page_obj": page_obj,
        "status_filter": status_filter,
        "status_tabs": status_tabs,
        "total_orders": Order.objects.filter(shop=shop).count(),
        "low_stock_count": low_stock_count,
        "latest_seen_id": latest_seen_id,
        "open_orders_count": open_orders_count,
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
        order.transition_status(new_status, actor=request.user)
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


@login_required
def low_stock_view(request, shop_slug):
    """
    Day 17 — dedicated low-stock list for a single shop.

    Same LOW_STOCK_FILTER as the badge counts above, so a product that
    shows up in the shop switcher's badge or the order-list banner is
    guaranteed to also appear here — one definition of "low stock",
    not three that could drift out of sync.

    Ordered by stock_quantity ascending: the shop's most urgent restock
    need (1 left) surfaces above a less urgent one (5 left).
    """
    shop = get_object_or_404(Shop, slug=shop_slug, owner=request.user)

    low_stock_products = (
        Product.objects
        .filter(category__shop=shop, **LOW_STOCK_FILTER)
        .select_related("category")
        .order_by("stock_quantity", "name")
    )

    return render(request, "dashboard/low_stock.html", {
        "shop": shop,
        "products": low_stock_products,
    })


@login_required
def new_orders_check_view(request, shop_slug):
    """
    Day 20 — polled by HTMX every 15s from shop_orders.html.

    Deliberately a COUNT query only, not a fetch of the actual new
    orders — this runs on a timer for as long as the owner has the
    tab open, so it needs to be cheap. Returns a small HTML partial
    (not JSON) since it's swapped directly into the page by HTMX; a
    non-intrusive banner rather than an auto-refreshing list, so a
    poll landing mid-click on an order doesn't yank the page out from
    under the owner.

    ?since=<order id> is the cutoff (the max order id that existed
    when the page was loaded — see shop_orders_view's latest_seen_id).
    ?status is passed through only so the "tap to refresh" link can
    preserve whatever status tab the owner is currently on.
    """
    shop = get_object_or_404(Shop, slug=shop_slug, owner=request.user)

    try:
        since_id = int(request.GET.get("since", "0"))
    except (TypeError, ValueError):
        since_id = 0

    status_filter = request.GET.get("status", "")
    if status_filter not in OrderStatus.values:
        status_filter = ""

    new_count = Order.objects.filter(shop=shop, id__gt=since_id).count()

    return render(request, "dashboard/_new_orders_banner.html", {
        "shop": shop,
        "new_count": new_count,
        "status_filter": status_filter,
    })

