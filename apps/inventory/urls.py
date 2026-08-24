from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("movements/", views.MovementsHubView.as_view(), name="movements_hub"),
    path("receive/", views.ReceiveStockView.as_view(), name="receive_stock"),
    path("assets/", views.UnitAssetListView.as_view(), name="asset_list"),
    path("assets/<uuid:pk>/", views.UnitAssetDetailView.as_view(), name="asset_detail"),
    path("assets/<uuid:pk>/correct/", views.AdminCorrectUnitView.as_view(), name="asset_correct"),
    path("balances/", views.StockBalanceListView.as_view(), name="balance_list"),
    path("balances/<uuid:pk>/", views.StockBalanceDetailView.as_view(), name="balance_detail"),
    path(
        "balances/<uuid:pk>/correct/",
        views.AdminCorrectBalanceView.as_view(),
        name="balance_correct",
    ),
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
    path("assess-return/", views.AssessReturnView.as_view(), name="assess_return"),
    path("mark-damaged/", views.MarkDamagedView.as_view(), name="mark_damaged"),
    path("mark-lost/", views.MarkLostView.as_view(), name="mark_lost"),
    path("dispose/", views.DisposeView.as_view(), name="dispose"),
]
