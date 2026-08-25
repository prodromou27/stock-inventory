from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import ForcedPasswordChangeView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Shadows the "password_change" URL django.contrib.auth.urls defines below —
    # registered first so it wins both for incoming requests and for reverse()
    # (docs/architecture/04-permission-matrix.md's "Default admin bootstrap").
    path(
        "accounts/password_change/",
        ForcedPasswordChangeView.as_view(),
        name="password_change",
    ),
    path("accounts/", include("django.contrib.auth.urls")),
    path("manage/", include("apps.accounts.urls")),
    path("locations/", include("apps.locations.urls")),
    path("products/", include("apps.catalog.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("documents/", include("apps.documents.urls")),
    path("audit/", include("apps.audit.urls")),
    path("reports/", include("apps.reporting.urls")),
    path("imports/", include("apps.imports.urls")),
    path("exports/", include("apps.exports.urls")),
    path("", include("apps.core.urls")),
]
