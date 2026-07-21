import io

import qrcode
import qrcode.image.svg

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from shops.models import Shop


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _get_short_url(request, shop):
    """
    Builds the full absolute short URL that will be encoded into the
    QR code: https://<host>/s/<shop_id>/

    Uses request.build_absolute_uri() so it works correctly in both
    local dev (http://127.0.0.1:8000/s/5/) and production
    (https://sajilopasal.com/s/5/) without hardcoding the domain.

    Why /s/<id>/ rather than the full slug URL:
    - Numeric-only paths after the prefix keep the QR code in
      alphanumeric encoding mode, producing fewer, larger modules
      (pixels) per character — physically blockier and easier for
      cheap Android camera lenses to resolve under poor lighting.
    - If the shop is ever renamed (slug changes), the QR code still
      works because it encodes the immutable numeric PK, not the slug.
    """
    return request.build_absolute_uri(f"/s/{shop.pk}/")


def _build_qr(data, error_correction=qrcode.constants.ERROR_CORRECT_M):
    """
    Returns a configured qrcode.QRCode instance.

    ERROR_CORRECT_M = ~15% error correction — good balance between
    density and damage tolerance. A QR code on a physical menu may get
    coffee-stained or creased; M level recovers from that while keeping
    the code scannable on cheap cameras (L would be too fragile, H too
    dense for low-end lenses).
    """
    qr = qrcode.QRCode(
        version=None,           # auto-select smallest version that fits
        error_correction=error_correction,
        box_size=10,
        border=4,               # 4-module quiet zone per QR spec minimum
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr


# ─────────────────────────────────────────────
# Short redirect — the URL encoded in the QR code
# ─────────────────────────────────────────────

def short_redirect_view(request, shop_id):
    """
    /s/<shop_id>/ → redirects to the public customer menu.

    This is the URL physically printed in the QR code. It's intentionally
    kept separate from the QR generation views so:
    - The redirect works for anyone (no login required) — customers
      scanning the QR code aren't authenticated.
    - The destination can be changed (e.g. from a placeholder to the
      real Day 9 menu) without reprinting any QR codes.
    - Inactive shops return 404 — a shop that's been deactivated should
      not serve a menu to customers who scan an old QR code.
    """
    shop = get_object_or_404(Shop, pk=shop_id, is_active=True)
    return redirect(shop.get_menu_url())


# ─────────────────────────────────────────────
# QR code generation — owner-only, login required
# ─────────────────────────────────────────────

@login_required
@require_GET
def qr_png_view(request, shop_slug):
    """
    Generates and serves a QR code as a PNG image for the given shop.
    Owner-only: a shop owned by someone else returns 404.

    The encoded URL is the short /s/<id>/ redirect, not the full slug
    URL — keeps the QR code sparse and low-density for cheap cameras.

    Response is served directly as image/png with cache headers so the
    browser can cache it (the QR code for a given shop never changes
    unless the shop PK somehow changes, which it can't).
    """
    shop = get_object_or_404(Shop, slug=shop_slug, owner=request.user)
    short_url = _get_short_url(request, shop)

    qr = _build_qr(short_url)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    response = HttpResponse(buf.read(), content_type="image/png")
    response["Content-Disposition"] = f'inline; filename="qr-{shop.slug}.png"'
    response["Cache-Control"] = "private, max-age=86400"  # cache 24h client-side
    return response


@login_required
@require_GET
def qr_svg_view(request, shop_slug):
    """
    Generates and serves a QR code as an SVG for the given shop.
    SVG is vector — scales to any size without pixelation, making it
    suitable for printing on paper menus and posters.

    Uses qrcode's SvgPathImage factory which produces a compact single-
    path SVG rather than individual rect elements per module — smaller
    file, faster rendering in the browser/PDF.
    """
    shop = get_object_or_404(Shop, slug=shop_slug, owner=request.user)
    short_url = _get_short_url(request, shop)

    qr = _build_qr(short_url)
    factory = qrcode.image.svg.SvgPathImage
    img = qr.make_image(image_factory=factory)

    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)

    response = HttpResponse(buf.read(), content_type="image/svg+xml")
    response["Content-Disposition"] = f'attachment; filename="qr-{shop.slug}.svg"'
    response["Cache-Control"] = "private, max-age=86400"
    return response


@login_required
@require_GET
def qr_detail_view(request, shop_slug):
    """
    HTML page showing the QR code for a shop, with download buttons.
    The actual QR image is served by qr_png_view (via an <img> tag)
    rather than embedded directly — keeps this view simple and lets
    the browser cache the QR image independently.
    """
    shop = get_object_or_404(Shop, slug=shop_slug, owner=request.user)
    short_url = _get_short_url(request, shop)
    return render(request, "qr_manager/qr_detail.html", {
        "shop": shop,
        "short_url": short_url,
    })


@login_required
@require_GET
def qr_download_png_view(request, shop_slug):
    """
    Same as qr_png_view but forces a download (Content-Disposition:
    attachment) instead of inline display — for the "Download PNG"
    button in the owner dashboard.
    """
    shop = get_object_or_404(Shop, slug=shop_slug, owner=request.user)
    short_url = _get_short_url(request, shop)

    qr = _build_qr(short_url)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    response = HttpResponse(buf.read(), content_type="image/png")
    response["Content-Disposition"] = f'attachment; filename="qr-{shop.slug}.png"'
    response["Cache-Control"] = "private, max-age=86400"
    return response