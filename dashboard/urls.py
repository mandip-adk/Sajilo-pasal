from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.dashboard_home_view, name="home"),
    path("<slug:shop_slug>/", views.shop_orders_view, name="shop_orders"),
    path("<slug:shop_slug>/orders/<int:order_id>/", views.order_detail_view, name="order_detail"),
]
