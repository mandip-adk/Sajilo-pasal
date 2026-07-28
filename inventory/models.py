from django.conf import settings
from django.db import models

from products.models import Product
from shops.models import Shop


class InventoryLogReason(models.TextChoices):
    INITIAL_STOCK = "initial_stock", "Initial Stock"
    SALE          = "sale",         "Sale"
    CANCELLATION  = "cancellation", "Cancelled (Restored)"
    MANUAL        = "manual",       "Manual Adjustment"


class InventoryLog(models.Model):
    """
    Day 16 — an append-only audit trail of every stock_quantity change,
    however it happened.

    Product.adjust_stock() deliberately stays a pure, unopinionated
    "change the number safely" primitive (see its own docstring — it
    explicitly doesn't enforce business rules, that's the caller's
    job). This model follows the same philosophy: it doesn't live
    inside adjust_stock() itself, it's written by the caller alongside
    the adjustment, via InventoryLog.record() below. That keeps
    adjust_stock() reusable for any future caller that has no interest
    in an audit trail, while every caller that DOES care (order
    Preparing/Cancelled, manual restock, initial stock at creation)
    gets one consistent log shape.

    shop and product_name are denormalized/snapshotted on purpose —
    same pattern as OrderItem.product_name/unit_price. product is
    SET_NULL, so a deleted product must not take its shop's inventory
    history down with it, and a log entry should still read sensibly
    after a product is later renamed or removed.
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_logs",
    )
    product_name = models.CharField(max_length=150)

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="inventory_logs",
    )

    reason = models.CharField(max_length=20, choices=InventoryLogReason.choices)
    delta = models.IntegerField()             # signed: negative = decrease, positive = increase
    resulting_stock = models.IntegerField()   # snapshot of stock_quantity AFTER this change
    note = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Inventory Log"
        verbose_name_plural = "Inventory Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shop", "-created_at"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        sign = "+" if self.delta >= 0 else ""
        return f"{self.product_name}: {sign}{self.delta} ({self.get_reason_display()})"

    @classmethod
    def record(cls, product, delta, reason, actor=None, note=""):
        """
        The single entry point for any stock change that should be
        audited. Adjusts stock via product.adjust_stock() — inheriting
        its select_for_update() race-safety rather than re-implementing
        it — then writes the resulting InventoryLog row referencing the
        stock_quantity adjust_stock() actually returned.

        Not wrapped in its own transaction.atomic() — callers that need
        this coupled with other writes in the same transaction (e.g.
        Order.transition_status() also saving the order's new status)
        should call this from inside their own atomic block. See
        Order._adjust_stock_for_items() for that pattern.
        """
        new_stock = product.adjust_stock(delta)
        return cls.objects.create(
            product=product,
            product_name=product.name,
            shop=product.category.shop,
            reason=reason,
            delta=delta,
            resulting_stock=new_stock,
            note=note,
            created_by=actor,
        )


    