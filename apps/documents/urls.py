from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path(
        "transactions/<uuid:pk>/generate/",
        views.GenerateDocumentView.as_view(),
        name="generate_document",
    ),
    path(
        "transactions/<uuid:pk>/attach/",
        views.AttachmentUploadView.as_view(),
        name="attachment_upload",
    ),
    path("<uuid:pk>/", views.DocumentDetailView.as_view(), name="document_detail"),
    path("<uuid:pk>/download/", views.DocumentDownloadView.as_view(), name="document_download"),
    path(
        "<uuid:pk>/regenerate/", views.RegenerateDocumentView.as_view(), name="regenerate_document"
    ),
    path(
        "attachments/<uuid:pk>/download/",
        views.AttachmentDownloadView.as_view(),
        name="attachment_download",
    ),
    path(
        "attachments/<uuid:pk>/delete/",
        views.AttachmentDeleteView.as_view(),
        name="attachment_delete",
    ),
    path("templates/", views.DocumentTemplateHubView.as_view(), name="template_hub"),
    path(
        "templates/<str:document_type>/",
        views.DocumentTemplateEditView.as_view(),
        name="template_edit",
    ),
    path(
        "templates/<str:document_type>/preview/",
        views.DocumentTemplatePreviewView.as_view(),
        name="template_preview",
    ),
    path(
        "templates/<str:document_type>/reset/",
        views.DocumentTemplateResetView.as_view(),
        name="template_reset",
    ),
]
