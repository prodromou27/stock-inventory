from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("movements/", views.MovementsHubView.as_view(), name="movements_hub"),
    path("receive/", views.ReceiveStockView.as_view(), name="receive_stock"),
    path("receive/quick/", views.QuickReceiveView.as_view(), name="quick_receive"),
    path("receive/bulk/", views.ReceiveBulkView.as_view(), name="receive_bulk"),
    path("products/grid-data/", views.ProductGridDataView.as_view(), name="product_grid_data"),
    path("assets/", views.UnitAssetListView.as_view(), name="asset_list"),
    path("assets/grid-data/", views.UnitAssetGridDataView.as_view(), name="asset_grid_data"),
    path(
        "assets/<uuid:pk>/grid-field/",
        views.AssetGridFieldUpdateView.as_view(),
        name="asset_grid_field_update",
    ),
    path("assets/<uuid:pk>/", views.UnitAssetDetailView.as_view(), name="asset_detail"),
    path("assets/<uuid:pk>/correct/", views.AdminCorrectUnitView.as_view(), name="asset_correct"),
    path(
        "assets/<uuid:pk>/reclassify/",
        views.UnitPurposeReclassifyView.as_view(),
        name="asset_reclassify_purpose",
    ),
    path(
        "assets/<uuid:pk>/install-component/",
        views.InstallComponentView.as_view(),
        name="install_component",
    ),
    path(
        "assets/<uuid:pk>/remove-component/",
        views.RemoveComponentView.as_view(),
        name="remove_component",
    ),
    path("assets/picker-data/", views.AssetPickerDataView.as_view(), name="asset_picker_data"),
    path(
        "balances/picker-data/", views.BalancePickerDataView.as_view(), name="balance_picker_data"
    ),
    path(
        "grid-views/<str:grid_key>/",
        views.SavedGridViewListCreateView.as_view(),
        name="saved_grid_view_list_create",
    ),
    path(
        "grid-views/<uuid:pk>/update/",
        views.SavedGridViewUpdateView.as_view(),
        name="saved_grid_view_update",
    ),
    path(
        "grid-views/<uuid:pk>/delete/",
        views.SavedGridViewDeleteView.as_view(),
        name="saved_grid_view_delete",
    ),
    path("balances/", views.StockBalanceListView.as_view(), name="balance_list"),
    path("balances/grid-data/", views.StockBalanceGridDataView.as_view(), name="balance_grid_data"),
    path("balances/<uuid:pk>/", views.StockBalanceDetailView.as_view(), name="balance_detail"),
    path(
        "balances/<uuid:pk>/correct/",
        views.AdminCorrectBalanceView.as_view(),
        name="balance_correct",
    ),
    path(
        "balances/<uuid:pk>/reclassify/",
        views.QuantityPurposeReclassifyView.as_view(),
        name="balance_reclassify_purpose",
    ),
    path("transactions/", views.TransactionListView.as_view(), name="transaction_list"),
    path(
        "transactions/<uuid:pk>/", views.TransactionDetailView.as_view(), name="transaction_detail"
    ),
    path(
        "transactions/<uuid:pk>/reverse/",
        views.AdminReverseTransactionView.as_view(),
        name="transaction_reverse",
    ),
    path("transactions/<uuid:pk>/return/", views.ReturnView.as_view(), name="return_stock"),
    path("transfer/", views.TransferView.as_view(), name="transfer"),
    path("reserve/", views.ReserveView.as_view(), name="reserve"),
    path("reservations/", views.ReservationListView.as_view(), name="reservation_list"),
    path(
        "reservations/<uuid:pk>/", views.ReservationDetailView.as_view(), name="reservation_detail"
    ),
    path(
        "reservations/<uuid:pk>/release/",
        views.ReleaseReservationView.as_view(),
        name="release_reservation",
    ),
    path("assign/", views.AssignView.as_view(), name="assign"),
    path("deliver/", views.DeliverView.as_view(), name="deliver"),
    path("customers/search/", views.CustomerSearchDataView.as_view(), name="customer_search_data"),
    path("assess-return/", views.AssessReturnView.as_view(), name="assess_return"),
    path("mark-damaged/", views.MarkDamagedView.as_view(), name="mark_damaged"),
    path("repair-damaged/", views.RepairDamagedView.as_view(), name="repair_damaged"),
    path("mark-lost/", views.MarkLostView.as_view(), name="mark_lost"),
    path("dispose/", views.DisposeView.as_view(), name="dispose"),
]
