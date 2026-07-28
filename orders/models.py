import uuid

from django.db import models, transaction
from django.utils import timezone

from inventory.models import InventoryLog, InventoryLogReason


class OrderStatus(models.TextChoices):
    PENDING   = "pending",   "Pending"
    PREPARING = "preparing", "Preparing"
    READY     = "ready",     "Ready"
    CANCELLED = "cancelled", "Cancelled"


class PaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PAID   = "paid",   "Paid"


class PaymentMethod(models.TextChoices):
    CASH      = "cash",      "Cash"
    FONEPAY   = "fonepay",   "Fonepay QR"
    # eSewa and Khalti reserved for future enhancement per SDD roadmap


class OrderTransitionError(Exception):
    """
    Raised when Order.transition_status() is asked to move an order
    between two statuses that aren't a valid next step (e.g. Ready ->
    Preparing, or Cancelled -> anything). Deliberately a hard error
    rather than a silent no-op, so a bug in the calling view surfaces
    immediately instead of quietly failing to update an order.
    """
    pass


class OrderPaymentError(Exception):
    """
    Raised by Order.update_payment() for an invalid payment_status /
    payment_method value, or for a payment_status change attempted on
    a cancelled order (there's nothing left to collect on a cancelled
    order — see update_payment()'s docstring for the payment_method
    exception to that rule).
    """
    pass


# Day 14 — the order status state machine.
#
# READY and CANCELLED are terminal: once food is ready or an order is
# cancelled, there's no path back. This matches how a kitchen actually
# works — an owner doesn't "un-ready" a plate, and cancelling after
# pickup/serving isn't a real scenario worth supporting here.
ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING:   {OrderStatus.PREPARING, OrderStatus.CANCELLED},
    OrderStatus.PREPARING: {OrderStatus.READY, OrderStatus.CANCELLED},
    OrderStatus.READY:     set(),
    OrderStatus.CANCELLED: set(),
}


class Order(models.Model):
    """
    A customer order placed from the public menu page.

    Key design decisions:
    - order_number: sequential per-shop (1, 2, 3...) — human-readable,
      what the owner calls out to the customer. Generated atomically
      inside a select_for_update() transaction to prevent two
      simultaneous orders getting the same number.
    - order_token: a UUID issued per order submission, used to detect
      and reject duplicate form submissions (e.g. customer double-taps
      "Place Order" on a slow connection). Checked before saving.
    - subtotal is stored at order time — not recomputed from products
      later, since product prices may change after the order is placed.
    - OrderItems store unit_price at order time for the same reason.
    """

    shop          = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="orders",
    )

    order_number  = models.PositiveIntegerField()   # sequential per shop
    order_token   = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
    )

    # Customer-provided fields
    table_number  = models.CharField(max_length=50, blank=True)
    customer_note = models.TextField(blank=True)

    # Status
    status         = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )

    # Financials — stored at order time, not recomputed later
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Order"
        verbose_name_plural = "Orders"
        ordering            = ["-created_at"]
        # order_number is unique per shop, not globally
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "order_number"],
                name="unique_order_number_per_shop",
            ),
        ]
        indexes = [
            models.Index(fields=["shop", "status"]),
            models.Index(fields=["shop", "created_at"]),
        ]

    def __str__(self):
        return f"Order #{self.order_number:03d} — {self.shop.name}"

    @property
    def order_number_display(self):
        """Zero-padded for display: #001, #042, #100 etc."""
        return f"#{self.order_number:03d}"

    @classmethod
    def get_next_order_number(cls, shop):
        """
        Atomically computes the next sequential order number for this shop.

        Uses select_for_update() on the shop's most recent order to
        prevent two simultaneous orders from receiving the same number —
        the exact race condition the SDD review flagged. Both requests
        would otherwise read max(order_number)=5 simultaneously and
        both try to insert order_number=6, causing an IntegrityError
        on the UniqueConstraint(shop, order_number).

        Must be called inside a transaction.atomic() block — the caller
        (create_from_cart) wraps the whole order creation in one.
        """
        last = (
            cls.objects
            .select_for_update()
            .filter(shop=shop)
            .order_by("-order_number")
            .first()
        )
        return (last.order_number + 1) if last else 1

    @classmethod
    def create_from_cart(cls, shop, cart_items, table_number="",
                         customer_note="", payment_method=PaymentMethod.CASH,
                         order_token=None):
        """
        Creates an Order and its OrderItems from the cart payload in a
        single atomic transaction. The entire create is wrapped so:
        - order_number generation (select_for_update) is race-safe
        - if any OrderItem save fails, the whole order rolls back
        - stock is NOT decremented here — that happens in
          transition_status() when the owner marks an order as
          'Preparing' (confirming the order is real before consuming
          stock), added on Day 14

        cart_items: list of dicts with keys:
            id       — product id (str or int)
            name     — product name at order time
            price    — unit price at order time (Decimal or str)
            quantity — quantity ordered (int)

        Returns the created Order instance.
        """
        from decimal import Decimal
        from products.models import Product

        with transaction.atomic():
            order_number = cls.get_next_order_number(shop)

            order = cls.objects.create(
                shop=shop,
                order_number=order_number,
                order_token=order_token or uuid.uuid4(),
                table_number=table_number,
                customer_note=customer_note,
                payment_method=payment_method,
                subtotal=Decimal("0"),
            )

            subtotal = Decimal("0")
            for item in cart_items:
                product = Product.objects.get(pk=item["id"])
                unit_price = Decimal(str(item["price"]))
                quantity   = int(item["quantity"])
                line_total = unit_price * quantity

                OrderItem.objects.create(
                    order=order,
                    product=product,
                    product_name=item["name"],  # snapshot at order time
                    quantity=quantity,
                    unit_price=unit_price,
                    line_total=line_total,
                )
                subtotal += line_total

            order.subtotal = subtotal
            order.save(update_fields=["subtotal"])

        return order

    def transition_status(self, new_status, actor=None):
        """
        Day 14 — the single place order status changes happen.

        Validates the transition against ALLOWED_TRANSITIONS, then:
          - PENDING -> PREPARING: decrements stock for every line item
            whose product still exists. product FK is SET_NULL, so an
            item whose product was later deleted is simply skipped —
            there's nothing left to decrement against.
          - PREPARING -> CANCELLED: restores the stock that was taken
            on the Preparing transition, so a cancelled order doesn't
            permanently understate inventory.
          - Every other allowed transition (PENDING -> CANCELLED,
            PREPARING -> READY) has no stock effect — stock was never
            touched for an order that's still Pending.

        Day 16: both stock-touching transitions above now also write
        an InventoryLog entry per item, via InventoryLog.record(). The
        optional `actor` (the logged-in owner making the change, from
        request.user in the dashboard view) is attributed on those log
        rows — pass it through when calling this from a view.

        Runs inside select_for_update() on this order row so two
        concurrent status-change requests for the same order (e.g. a
        double-tap on the dashboard) serialize instead of both reading
        the same starting status and both trying to decrement stock.

        Raises OrderTransitionError for any transition not in
        ALLOWED_TRANSITIONS. Returns self, refreshed to the new status.
        """
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=self.pk)

            allowed = ALLOWED_TRANSITIONS.get(locked.status, set())
            if new_status not in allowed:
                raise OrderTransitionError(
                    f"Cannot move order {locked.order_number_display} "
                    f"from '{locked.status}' to '{new_status}'."
                )

            if new_status == OrderStatus.PREPARING:
                locked._adjust_stock_for_items(
                    sign=-1, reason=InventoryLogReason.SALE, actor=actor,
                )
            elif new_status == OrderStatus.CANCELLED and locked.status == OrderStatus.PREPARING:
                locked._adjust_stock_for_items(
                    sign=+1, reason=InventoryLogReason.CANCELLATION, actor=actor,
                )

            locked.status = new_status
            locked.save(update_fields=["status"])

            self.status = locked.status
            return self

    def _adjust_stock_for_items(self, sign, reason, actor=None):
        """
        sign: -1 to decrement (moving to Preparing), +1 to restore
        (cancelling out of Preparing).

        Day 16: routes through InventoryLog.record() instead of
        calling item.product.adjust_stock() directly, so every stock
        change here leaves an audit trail (product, delta, resulting
        stock, reason, who did it). record() still calls
        adjust_stock() underneath — this doesn't change the race-safety
        (select_for_update on the product row), it just also writes
        the log row alongside it.
        """
        for item in self.items.select_related("product__category__shop").all():
            if item.product_id is None:
                continue
            InventoryLog.record(
                product=item.product,
                delta=sign * item.quantity,
                reason=reason,
                actor=actor,
                note=f"Order {self.order_number_display}",
            )

    def update_payment(self, payment_status=None, payment_method=None):
        """
        Day 15 — payment status/method tracking.

        Both arguments are optional so a caller can update either
        independently:
            order.update_payment(payment_status=PaymentStatus.PAID)
            order.update_payment(payment_method=PaymentMethod.FONEPAY)
            order.update_payment(payment_status=PaymentStatus.PAID,
                                  payment_method=PaymentMethod.FONEPAY)

        payment_status changes are blocked once an order is Cancelled
        — there's nothing left to collect. payment_method corrections
        remain allowed regardless of order status: a customer said
        "cash" at checkout but actually paid Fonepay is a bookkeeping
        correction, not a business-rule violation, and the owner may
        legitimately want to fix that record even after cancellation.

        Runs inside select_for_update() on the order row, consistent
        with transition_status(), so a payment update racing a status
        update (or another payment update) for the same order
        serializes rather than one silently clobbering the other.

        Raises OrderPaymentError for an invalid choice value, or for a
        payment_status change on a cancelled order. Returns self,
        refreshed with the new values.

        Note on combining both arguments: if payment_status is
        rejected (cancelled order), the whole call rolls back —
        including any payment_method change passed in the same call —
        since both run inside one atomic block. In practice the
        dashboard UI submits these as two separate forms/requests, so
        this only matters if a caller deliberately batches them.
        """
        if payment_status is not None and payment_status not in PaymentStatus.values:
            raise OrderPaymentError(f"'{payment_status}' is not a valid payment status.")
        if payment_method is not None and payment_method not in PaymentMethod.values:
            raise OrderPaymentError(f"'{payment_method}' is not a valid payment method.")

        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=self.pk)
            update_fields = []

            if payment_status is not None:
                if locked.status == OrderStatus.CANCELLED:
                    raise OrderPaymentError(
                        f"Cannot update payment status for cancelled order "
                        f"{locked.order_number_display}."
                    )
                locked.payment_status = payment_status
                update_fields.append("payment_status")

            if payment_method is not None:
                locked.payment_method = payment_method
                update_fields.append("payment_method")

            if update_fields:
                locked.save(update_fields=update_fields)

            self.payment_status = locked.payment_status
            self.payment_method = locked.payment_method
            return self


class OrderItem(models.Model):
    """
    A single line item in an order.

    product_name and unit_price are stored as snapshots at order time —
    if the owner later renames or reprices a product, historical orders
    still show what the customer actually ordered and paid for.

    product FK is nullable (null=True, on_delete=SET_NULL) so that
    deleting a product doesn't cascade-delete order history.
    """

    order        = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product      = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
    )

    # Snapshots — preserved even if the product is later edited/deleted
    product_name = models.CharField(max_length=150)
    unit_price   = models.DecimalField(max_digits=10, decimal_places=2)
    quantity     = models.PositiveIntegerField()
    line_total   = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name        = "Order Item"
        verbose_name_plural = "Order Items"

    def __str__(self):
        return f"{self.quantity}x {self.product_name} (Order #{self.order.order_number:03d})"

    