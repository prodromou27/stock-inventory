from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("receive/", views.ReceiveStockView.as_view(), name="receive_stock"),
    path("assets/", views.UnitAssetListView.as_view(), name="asset_list"),
    path("assets/<uuid:pk>/", views.UnitAssetDetailView.as_view(), name="asset_detail"),
    path("balances/", views.StockBalanceListView.as_view(), name="balance_list"),
    path("balances/<uuid:pk>/", views.StockBalanceDetailView.as_view(), name="balance_detail"),
    path(
        "transactions/<uuid:pk>/", views.TransactionDetailView.as_view(), name="transaction_detail"
    ),
]
