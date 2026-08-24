from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("manage/", include("apps.accounts.urls")),
    path("locations/", include("apps.locations.urls")),
    path("", include("apps.core.urls")),
]
