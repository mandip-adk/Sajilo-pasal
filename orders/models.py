import uuid

from django.db import models, transaction
from django.utils import timezone


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
        - stock is NOT decremented here — that happens on Day 12 when
          the owner marks an order as 'Preparing' (confirming the order
          is real before consuming stock)

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

    