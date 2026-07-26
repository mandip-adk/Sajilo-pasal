from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("<slug:shop_slug>/place/", views.place_order_view, name="place"),
]
