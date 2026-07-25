from django.contrib import admin
from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model       = OrderItem
    extra       = 0
    readonly_fields = ["product", "product_name", "unit_price", "quantity", "line_total"]
    can_delete  = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display    = [
        "order_number_display", "shop", "status", "payment_status",
        "payment_method", "subtotal", "table_number", "created_at",
    ]
    list_filter     = ["status", "payment_status", "payment_method", "shop"]
    search_fields   = [
        "order_number", "table_number",
        "shop__name", "shop__owner__email",
    ]
    readonly_fields = [
        "order_number", "order_token", "subtotal", "created_at", "updated_at",
    ]
    ordering        = ["-created_at"]
    inlines         = [OrderItemInline]

    fieldsets = (
        ("Order Info", {
            "fields": ("shop", "order_number", "order_token", "table_number", "customer_note"),
        }),
        ("Status", {
            "fields": ("status", "payment_status", "payment_method", "subtotal"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display  = ["product_name", "quantity", "unit_price", "line_total", "order"]
    search_fields = ["product_name", "order__order_number", "order__shop__name"]
    readonly_fields = ["order", "product", "product_name", "unit_price", "quantity", "line_total"]

    