from django.urls import path
from . import views

app_name = "qr_manager"

urlpatterns = [
    # Short redirect — encoded in the physical QR code
    path("<int:shop_id>/", views.short_redirect_view, name="redirect"),

    # QR detail page — owner-facing HTML with download buttons
    path("qr/<slug:shop_slug>/",   views.qr_detail_view,  name="detail"),

    # QR code generation — raw image responses, owner-only
    path("qr/<slug:shop_slug>/png/",   views.qr_png_view,   name="png"),
    path("qr/<slug:shop_slug>/svg/", views.qr_svg_view, name="svg"),
    path("qr/<slug:shop_slug>/download/png/", views.qr_download_png_view, name="download_png"),
]

