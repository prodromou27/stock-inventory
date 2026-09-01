from django.urls import path

from . import views

app_name = "reporting"

urlpatterns = [
    path("", views.ReportsHubView.as_view(), name="hub"),
    path("current-stock/", views.CurrentStockView.as_view(), name="current_stock"),
    path("stock-by-location/", views.StockByLocationView.as_view(), name="stock_by_location"),
    path("reserved-stock/", views.ReservedStockView.as_view(), name="reserved_stock"),
    path(
        "employee-assignments/",
        views.EmployeeAssignmentsView.as_view(),
        name="employee_assignments",
    ),
    path(
        "customer-deliveries/", views.CustomerDeliveriesView.as_view(), name="customer_deliveries"
    ),
    path(
        "stock-by-project-reference/",
        views.StockByProjectReferenceView.as_view(),
        name="stock_by_project_reference",
    ),
    path(
        "temporary-assignments/",
        views.TemporaryAssignmentsView.as_view(),
        name="temporary_assignments",
    ),
    path("damaged-assets/", views.DamagedAssetsView.as_view(), name="damaged_assets"),
    path("lost-assets/", views.LostAssetsView.as_view(), name="lost_assets"),
    path("disposed-items/", views.DisposedItemsView.as_view(), name="disposed_items"),
    path("movement-history/", views.MovementHistoryView.as_view(), name="movement_history"),
    path("low-stock/", views.LowStockView.as_view(), name="low_stock"),
    path("custom/", views.SavedReportListView.as_view(), name="saved_report_list"),
    path("custom/new/", views.ReportBuilderStartView.as_view(), name="builder_start"),
    path("custom/build/", views.ReportBuilderView.as_view(), name="builder"),
    path("custom/<uuid:pk>/", views.SavedReportRunView.as_view(), name="saved_report_run"),
    path(
        "custom/<uuid:pk>/delete/",
        views.SavedReportDeleteView.as_view(),
        name="saved_report_delete",
    ),
]
