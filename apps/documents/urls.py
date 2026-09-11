from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path(
        "templates/<str:document_type>/designer/",
        views.VisualDocumentDesignerView.as_view(),
        name="template_designer",
    ),
    path(
        "transactions/<uuid:pk>/generate/",
        views.GenerateDocumentView.as_view(),
        name="generate_document",
    ),
    path(
        "transactions/<uuid:pk>/queue/",
        views.QueueDocumentView.as_view(),
        name="queue_document",
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
        "templates/<str:document_type>/live-preview/",
        views.DocumentTemplateLivePreviewView.as_view(),
        name="template_live_preview",
    ),
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
    path(
        "templates/<str:document_type>/submit-for-review/",
        views.DocumentTemplateSubmitForReviewView.as_view(),
        name="template_submit_for_review",
    ),
    path(
        "templates/<str:document_type>/reject-review/",
        views.DocumentTemplateRejectReviewView.as_view(),
        name="template_reject_review",
    ),
    path(
        "templates/<str:document_type>/publish/",
        views.DocumentTemplatePublishView.as_view(),
        name="template_publish",
    ),
    path(
        "templates/<str:document_type>/duplicate/",
        views.DocumentTemplateDuplicateView.as_view(),
        name="template_duplicate",
    ),
    path(
        "templates/<str:document_type>/apply-starter/",
        views.DocumentTemplateApplyStarterView.as_view(),
        name="template_apply_starter",
    ),
    path(
        "templates/<str:document_type>/versions/<uuid:version_pk>/restore/",
        views.DocumentTemplateRestoreVersionView.as_view(),
        name="template_restore_version",
    ),
    path("pdf-health/", views.PdfHealthDiagnosticsView.as_view(), name="pdf_health"),
    path("branding/", views.CountryBrandingListView.as_view(), name="branding_list"),
    path(
        "branding/<uuid:country_id>/",
        views.CountryBrandingEditView.as_view(),
        name="branding_edit",
    ),
]
