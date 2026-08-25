from django.urls import path

from . import views

app_name = "settings"

urlpatterns = [
    path("", views.SettingsHubView.as_view(), name="hub"),
    path("system/", views.SystemConfigurationView.as_view(), name="system"),
    path("certificates/", views.CertificateUploadView.as_view(), name="certificates"),
]
