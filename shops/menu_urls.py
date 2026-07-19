from django.urls import path
from . import menu_views

app_name = "menu"

urlpatterns = [
    # /shop/<slug>/  — public customer-facing menu
    # No login required. Mounted at "shop/" in config/urls.py.
    path("<slug:slug>/", menu_views.public_menu_view, name="detail"),
]
