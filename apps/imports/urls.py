from django.urls import path

from . import views

app_name = "imports"

urlpatterns = [
    path("", views.ImportBatchListView.as_view(), name="batch_list"),
    path("upload/", views.ImportUploadView.as_view(), name="upload"),
    path("template.csv", views.ImportTemplateDownloadView.as_view(), name="template_download"),
    path(
        "template.xlsx",
        views.ImportTemplateXlsxDownloadView.as_view(),
        name="template_xlsx_download",
    ),
    path("<uuid:pk>/", views.ImportBatchDetailView.as_view(), name="batch_detail"),
    path("<uuid:pk>/execute/", views.ImportExecuteView.as_view(), name="execute"),
    path(
        "<uuid:pk>/results.csv", views.ImportResultsDownloadView.as_view(), name="results_download"
    ),
    path(
        "<uuid:pk>/rows/<uuid:row_pk>/override-location/",
        views.ImportRowOverrideLocationView.as_view(),
        name="row_override_location",
    ),
    path(
        "<uuid:pk>/rows/<uuid:row_pk>/skip/",
        views.ImportRowSkipView.as_view(),
        name="row_skip",
    ),
    path(
        "<uuid:pk>/rows/<uuid:row_pk>/acknowledge-duplicate/",
        views.ImportRowAcknowledgeDuplicateView.as_view(),
        name="row_acknowledge_duplicate",
    ),
]
