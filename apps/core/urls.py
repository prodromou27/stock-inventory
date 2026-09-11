from django.urls import path

from . import job_views, views

app_name = "core"

urlpatterns = [
    path("jobs/", job_views.BackgroundJobListView.as_view(), name="job_list"),
    path("jobs/<uuid:pk>/", job_views.BackgroundJobDetailView.as_view(), name="job_detail"),
    path(
        "jobs/<uuid:pk>/download/",
        job_views.BackgroundJobDownloadView.as_view(),
        name="job_download",
    ),
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
