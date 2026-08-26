from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.ProductListView.as_view(), name="product_list"),
    path("new/", views.ProductCreateView.as_view(), name="product_create"),
    path("quick-add/", views.QuickAddProductsView.as_view(), name="quick_add"),
    path("grid/", views.ProductGridView.as_view(), name="product_grid"),
    path("<uuid:pk>/", views.ProductDetailView.as_view(), name="product_detail"),
    path("<uuid:pk>/edit/", views.ProductUpdateView.as_view(), name="product_update"),
    path(
        "custom-fields/",
        views.ProductCustomFieldDefinitionListView.as_view(),
        name="custom_field_list",
    ),
    path(
        "custom-fields/new/",
        views.ProductCustomFieldDefinitionCreateView.as_view(),
        name="custom_field_create",
    ),
    path(
        "custom-fields/<uuid:pk>/toggle-active/",
        views.ProductCustomFieldDefinitionToggleActiveView.as_view(),
        name="custom_field_toggle_active",
    ),
]
