from django.urls import path

from . import views

app_name = "exports"

urlpatterns = [
    path("settings/", views.ExportSettingsView.as_view(), name="settings"),
    path("settings/run-now/", views.RunExportNowView.as_view(), name="run_now"),
]
