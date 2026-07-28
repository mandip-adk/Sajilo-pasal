from django.contrib import admin

from .models import InventoryLog


@admin.register(InventoryLog)
class InventoryLogAdmin(admin.ModelAdmin):
    """
    Read-only in admin — InventoryLog is an append-only audit trail.
    Entries are only ever created programmatically via
    InventoryLog.record(), never hand-edited or hand-added here, since
    a log that can be edited after the fact isn't an audit trail.

    This is a stopgap viewer until the Day 16+ dashboard UI exists —
    lets you sanity-check that hooks are firing correctly without
    needing to query the shell.
    """
    list_display = (
        "created_at", "shop", "product_name", "reason",
        "delta", "resulting_stock", "created_by",
    )
    list_filter = ("reason", "shop")
    search_fields = ("product_name", "note")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    