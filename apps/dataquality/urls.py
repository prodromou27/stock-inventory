from django.urls import path

from . import views

app_name = "dataquality"

urlpatterns = [
    path("", views.DataQualityWorkspaceView.as_view(), name="workspace"),
    path("scan/", views.RunDetectionView.as_view(), name="run_detection"),
    path(
        "<uuid:pk>/resolve/",
        views.ResolveFindingView.as_view(),
        name="resolve_finding",
    ),
    path(
        "<uuid:pk>/dismiss/",
        views.DismissFindingView.as_view(),
        name="dismiss_finding",
    ),
    path(
        "<uuid:pk>/correct/",
        views.CorrectFindingView.as_view(),
        name="correct_finding",
    ),
]
