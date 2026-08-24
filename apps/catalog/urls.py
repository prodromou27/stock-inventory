from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.ProductListView.as_view(), name="product_list"),
    path("new/", views.ProductCreateView.as_view(), name="product_create"),
    path("<uuid:pk>/", views.ProductDetailView.as_view(), name="product_detail"),
    path("<uuid:pk>/edit/", views.ProductUpdateView.as_view(), name="product_update"),
]
