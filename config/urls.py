"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [
    path("admin/",  admin.site.urls),
    path("accounts/",   include("accounts.urls",  namespace="accounts")),
    path("dashboard/",  include("dashboard.urls", namespace="dashboard")),
    path("shops/", include("shops.urls", namespace="shops")),
    path("shops/", include("categories.urls", namespace="categories")),
    path("shops/", include("products.urls", namespace="products")),
    path("s/", include("qr_manager.urls", namespace="qr_manager")),
    path("shop/", include("shops.menu_urls", namespace="menu")),


]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


    