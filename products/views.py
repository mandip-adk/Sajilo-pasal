from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_POST
from django.core.exceptions import ValidationError

from shops.models import Shop
from categories.models import Category
from inventory.models import InventoryLog, InventoryLogReason
from .models import Product
from .forms import ProductForm


def _get_owned_shop_or_404(request, shop_slug):
    return get_object_or_404(Shop, slug=shop_slug, owner=request.user)


def _get_owned_category_or_404(request, shop_slug, category_id):
    """
    Same two-hop pattern as categories/views.py — category must belong
    to this shop, this shop must belong to this user, checked as one
    joined query.
    """
    return get_object_or_404(
        Category,
        pk=category_id,
        shop__slug=shop_slug,
        shop__owner=request.user,
    )


def _get_owned_product_or_404(request, shop_slug, category_id, product_id):
    """
    Three-hop ownership check: product -> category -> shop -> owner,
    all verified in a single joined query. A product that's real but
    sitting under a different category (even one the same user owns,
    via a different shop) returns 404 just as cleanly as a product
    that doesn't exist — same principle as the two-hop check in
    categories/views.py, extended one level deeper.
    """
    return get_object_or_404(
        Product,
        pk=product_id,
        category_id=category_id,
        category__shop__slug=shop_slug,
        category__shop__owner=request.user,
    )


@login_required
def product_list_view(request, shop_slug, category_id):
    shop = _get_owned_shop_or_404(request, shop_slug)
    category = _get_owned_category_or_404(request, shop_slug, category_id)
    products = category.products.all()
    return render(request, "products/product_list.html", {
        "shop": shop,
        "category": category,
        "products": products,
    })


@login_required
@require_http_methods(["GET", "POST"])
def product_create_view(request, shop_slug, category_id):
    shop = _get_owned_shop_or_404(request, shop_slug)
    category = _get_owned_category_or_404(request, shop_slug, category_id)
    form = ProductForm(request.POST or None, request.FILES or None, category=category)

    if request.method == "POST":
        if form.is_valid():
            try:
                form.instance.full_clean()
            except ValidationError as e:
                for field, errors in e.message_dict.items():
                    for error in errors:
                        form.add_error(field if field in form.fields else None, error)
            else:
                form.save()

                # Day 16 — log the opening stock balance, if any. This is
                # a direct .objects.create(), NOT InventoryLog.record():
                # record() calls adjust_stock() to *apply* a delta, but
                # here the stock value was already set and persisted by
                # form.save() above — calling record() too would apply
                # the same quantity a second time via the F() increment
                # in adjust_stock(). This just documents what save()
                # already did.
                if form.instance.stock_quantity:
                    InventoryLog.objects.create(
                        product=form.instance,
                        product_name=form.instance.name,
                        shop=shop,
                        reason=InventoryLogReason.INITIAL_STOCK,
                        delta=form.instance.stock_quantity,
                        resulting_stock=form.instance.stock_quantity,
                        note="Initial stock at creation",
                        created_by=request.user,
                    )

                messages.success(request, f'Product "{form.instance.name}" created.')
                return redirect("products:list", shop_slug=shop.slug, category_id=category.id)

        if form.errors:
            messages.error(request, "Please correct the errors below.")

    return render(request, "products/product_form.html", {
        "form": form,
        "shop": shop,
        "category": category,
        "is_edit": False,
    })


@login_required
@require_http_methods(["GET", "POST"])
def product_edit_view(request, shop_slug, category_id, product_id):
    shop = _get_owned_shop_or_404(request, shop_slug)
    category = _get_owned_category_or_404(request, shop_slug, category_id)
    product = _get_owned_product_or_404(request, shop_slug, category_id, product_id)

    # Day 16 — snapshot BEFORE binding the form. ProductForm(instance=product)
    # means form.is_valid() -> full_clean() writes cleaned_data straight onto
    # this same product instance, so product.stock_quantity already reflects
    # the NEW value by the time we're past the is_valid() check below. This
    # has to be captured here, or there's nothing left to diff against.
    original_stock_quantity = product.stock_quantity

    form = ProductForm(request.POST or None, request.FILES or None, instance=product, category=category)

    if request.method == "POST":
        if form.is_valid():
            try:
                form.instance.full_clean()
            except ValidationError as e:
                for field, errors in e.message_dict.items():
                    for error in errors:
                        form.add_error(field if field in form.fields else None, error)
            else:
                form.save()

                # Day 16 — log a manual stock adjustment if the owner
                # changed stock_quantity through this form. Same
                # direct-create reasoning as product_create_view above:
                # form.save() already persisted the new value, so this
                # documents the change rather than applying it again.
                new_stock_quantity = form.instance.stock_quantity
                delta = new_stock_quantity - original_stock_quantity
                if delta != 0:
                    InventoryLog.objects.create(
                        product=form.instance,
                        product_name=form.instance.name,
                        shop=shop,
                        reason=InventoryLogReason.MANUAL,
                        delta=delta,
                        resulting_stock=new_stock_quantity,
                        note="Stock updated via product edit form",
                        created_by=request.user,
                    )

                messages.success(request, "Product updated.")
                return redirect("products:list", shop_slug=shop.slug, category_id=category.id)

        if form.errors:
            messages.error(request, "Please correct the errors below.")

    return render(request, "products/product_form.html", {
        "form": form,
        "shop": shop,
        "category": category,
        "product": product,
        "is_edit": True,
    })


@login_required
@require_POST
def product_delete_view(request, shop_slug, category_id, product_id):
    product = _get_owned_product_or_404(request, shop_slug, category_id, product_id)
    name = product.name
    product.delete()
    messages.success(request, f'Product "{name}" deleted.')
    return redirect("products:list", shop_slug=shop_slug, category_id=category_id)


@login_required
@require_POST
def product_toggle_availability_view(request, shop_slug, category_id, product_id):
    """
    Quick owner action: flip is_available without going through the
    full edit form. Distinct from stock_quantity hitting zero — this
    is the owner's explicit "don't sell this right now" switch.
    """
    product = _get_owned_product_or_404(request, shop_slug, category_id, product_id)
    product.is_available = not product.is_available
    product.save(update_fields=["is_available"])
    state = "available" if product.is_available else "unavailable"
    messages.success(request, f'"{product.name}" marked as {state}.')
    return redirect("products:list", shop_slug=shop_slug, category_id=category_id)

    