from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods

from .models import Shop
from .forms import ShopForm


@login_required
def shop_list_view(request):
    """
    Lists only the current user's own shops. Ownership scoping starts
    at the query, not as an afterthought permission check.
    """
    shops = Shop.objects.filter(owner=request.user)
    return render(request, "shops/shop_list.html", {"shops": shops})


@login_required
@require_http_methods(["GET", "POST"])
def shop_create_view(request):
    form = ShopForm(request.POST or None, request.FILES or None)

    if request.method == "POST":
        if form.is_valid():
            shop = form.save(commit=False)
            shop.owner = request.user
            shop.save()
            messages.success(request, f'"{shop.name}" was created successfully.')
            return redirect("shops:detail", slug=shop.slug)
        else:
            messages.error(request, "Please correct the errors below.")

    return render(request, "shops/shop_form.html", {"form": form, "is_edit": False})


def _get_owned_shop_or_404(request, slug):
    """
    Fetches a shop by slug, but ONLY if it belongs to the requesting
    user. A shop that exists but belongs to someone else returns a 404
    — not a 403 — so as not to confirm to an attacker that a given slug
    even corresponds to a real shop at all.
    """
    return get_object_or_404(Shop, slug=slug, owner=request.user)


@login_required
def shop_detail_view(request, slug):
    shop = _get_owned_shop_or_404(request, slug)
    return render(request, "shops/shop_detail.html", {"shop": shop})


@login_required
@require_http_methods(["GET", "POST"])
def shop_edit_view(request, slug):
    shop = _get_owned_shop_or_404(request, slug)
    form = ShopForm(request.POST or None, request.FILES or None, instance=shop)

    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Shop details updated successfully.")
            return redirect("shops:detail", slug=shop.slug)
        else:
            messages.error(request, "Please correct the errors below.")

    return render(request, "shops/shop_form.html", {"form": form, "is_edit": True, "shop": shop})


@login_required
@require_http_methods(["GET", "POST"])
def shop_delete_view(request, slug):
    """
    Deleting a shop cascades to everything under it — categories,
    products, orders, and inventory logs are all FK CASCADE to Shop.
    This is destructive and irreversible, so we require the owner to
    type the exact shop name back to confirm before anything happens.
    GET just shows the confirmation page; nothing is deleted until POST.
    """
    shop = _get_owned_shop_or_404(request, slug)

    if request.method == "POST":
        confirmation = request.POST.get("confirm_name", "").strip()
        if confirmation != shop.name:
            messages.error(
                request,
                "Shop name didn't match. Nothing was deleted."
            )
            return render(request, "shops/shop_confirm_delete.html", {"shop": shop})

        shop_name = shop.name
        shop.delete()
        messages.success(request, f'"{shop_name}" and all its data were deleted.')
        return redirect("dashboard:home")

    return render(request, "shops/shop_confirm_delete.html", {"shop": shop})

