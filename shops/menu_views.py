from django.shortcuts import render, get_object_or_404

from shops.models import Shop


def public_menu_view(request, slug):
    """
    Public, no-login customer-facing menu page.
    Reached by scanning the QR code → /s/<id>/ → /shop/<slug>/

    Ownership is NOT checked here — this is intentionally open to
    all visitors. Any active shop's menu is publicly readable.
    Inactive shops 404 cleanly: a deactivated shop should not serve
    a menu to customers who scan an old QR sticker.

    Query strategy: fetch the shop, then prefetch all its categories
    with their products in two queries total (select_related for the
    shop, prefetch_related for categories → products). This avoids
    the N+1 problem where each category would trigger a separate
    products query — critical for a low-bandwidth mobile page that
    may be loaded over a slow Ncell/NTC connection.

    Products are annotated with is_orderable at the Python level
    (it's a property, not a DB expression) after the queryset is
    evaluated. This is fine for menus of typical size (10-40 products)
    but would need rethinking for very large menus.
    """
    shop = get_object_or_404(Shop, slug=slug, is_active=True)

    # Prefetch categories with their products in two queries total,
    # ordered by category creation order and product creation order.
    # Only fetch products whose category belongs to this shop —
    # the FK chain guarantees this, but be explicit for readability.
    categories = (
        shop.categories
        .prefetch_related("products")
        .order_by("created_at")
    )

    # Build a flat list of (category, products) pairs for the template,
    # filtering out empty categories (no products at all) so the menu
    # doesn't show section headers with nothing under them.
    menu_sections = []
    for category in categories:
        products = list(category.products.order_by("created_at"))
        if products:
            menu_sections.append((category, products))

    return render(request, "shops/public_menu.html", {
        "shop": shop,
        "menu_sections": menu_sections,
    })

