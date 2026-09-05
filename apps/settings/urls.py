from django.urls import path

from . import views

app_name = "settings"

urlpatterns = [
    path("", views.SettingsHubView.as_view(), name="hub"),
    path("system/", views.SystemConfigurationView.as_view(), name="system"),
    path("certificates/", views.CertificateUploadView.as_view(), name="certificates"),
    path("timezone/", views.TimezoneConfigurationView.as_view(), name="timezone"),
    path("smtp/", views.SmtpConfigurationView.as_view(), name="smtp"),
    path(
        "notifications/",
        views.NotificationSubscriptionListView.as_view(),
        name="notifications",
    ),
    path(
        "notifications/<uuid:pk>/edit/",
        views.NotificationSubscriptionUpdateView.as_view(),
        name="notification_edit",
    ),
]
