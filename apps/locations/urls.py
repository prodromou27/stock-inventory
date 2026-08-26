from django.urls import path

from . import views

app_name = "locations"

urlpatterns = [
    path("", views.LocationListView.as_view(), name="list"),
    path("new/", views.LocationCreateView.as_view(), name="create"),
    path("<uuid:pk>/", views.LocationDetailView.as_view(), name="detail"),
    path("<uuid:pk>/edit/", views.LocationEditView.as_view(), name="edit"),
    path(
        "<uuid:pk>/toggle-active/", views.LocationToggleActiveView.as_view(), name="toggle_active"
    ),
]
