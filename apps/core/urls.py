from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("healthz/", views.HealthCheckView.as_view(), name="health"),
    path("search/", views.GlobalSearchView.as_view(), name="search"),
    path("search/suggest/", views.SearchSuggestView.as_view(), name="search_suggest"),
    path(
        "dashboard-preferences/",
        views.DashboardPreferenceView.as_view(),
        name="dashboard_preferences",
    ),
    path(
        "dashboard-preferences/reset/",
        views.DashboardPreferenceResetView.as_view(),
        name="dashboard_preferences_reset",
    ),
    path("", views.HomeView.as_view(), name="home"),
]
