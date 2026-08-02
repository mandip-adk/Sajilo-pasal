from django.urls import path
from . import views

app_name = "shops"

urlpatterns = [
    path("",               views.shop_list_view,   name="list"),
    path("create/",        views.shop_create_view, name="create"),
    path("<slug:slug>/",   views.shop_detail_view, name="detail"),
    path("<slug:slug>/edit/", views.shop_edit_view, name="edit"),
    path("<slug:slug>/delete/", views.shop_delete_view, name="delete"),
]

