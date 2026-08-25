from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("manage/", include("apps.accounts.urls")),
    path("locations/", include("apps.locations.urls")),
    path("products/", include("apps.catalog.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("documents/", include("apps.documents.urls")),
    path("audit/", include("apps.audit.urls")),
    path("reports/", include("apps.reporting.urls")),
    path("imports/", include("apps.imports.urls")),
    path("", include("apps.core.urls")),
]
