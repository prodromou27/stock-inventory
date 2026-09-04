from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin

from .forms import ImportUploadForm, RowLocationOverrideForm
from .models import ImportBatch, ImportBatchStatus, ImportRow, ImportRowOutcome
from .services import (
    acknowledge_row_duplicate_serial,
    build_results_csv,
    build_template_csv,
    build_template_xlsx,
    create_batch_from_upload,
    execute_batch,
    set_row_location_override,
    skip_row,
)


class ImportBatchListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    allowed_roles = (ADMINISTRATOR,)
    model = ImportBatch
    template_name = "imports/batch_list.html"
    context_object_name = "batches"
    paginate_by = 25

    def get_queryset(self):
        return ImportBatch.objects.select_related("uploaded_by", "executed_by")


class ImportUploadView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "imports/upload.html"

    def get(self, request):
        return render(request, self.template_name, {"form": ImportUploadForm()})

    def post(self, request):
        form = ImportUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        try:
            batch, is_repeat_upload = create_batch_from_upload(
                uploaded_file=form.cleaned_data["file"],
                user=request.user,
                default_location=form.cleaned_data["default_location"],
                default_stock_purpose=form.cleaned_data["default_stock_purpose"],
            )
        except ValidationError as exc:
            form.add_error("file", exc)
            return render(request, self.template_name, {"form": form})

        if is_repeat_upload:
            messages.warning(
                request,
                "A completed import with the same file contents already exists — "
                "check you don't mean to re-import the same data.",
            )
        messages.success(request, f"Staged {batch.row_count()} row(s) for review.")
        return redirect(batch.get_absolute_url())


def _is_repeat_of_completed(batch):
    """Whether another COMPLETED batch shares this one's exact file
    contents — apps.imports.services.create_batch_from_upload()'s advisory
    checksum check, re-evaluated at execute time (not just upload time) so
    the confirmation gate below covers a batch left staged for a while
    before being executed, not just the moment it was uploaded.
    """
    return (
        ImportBatch.objects.filter(
            file_checksum=batch.file_checksum, status=ImportBatchStatus.COMPLETED
        )
        .exclude(pk=batch.pk)
        .exists()
    )


class ImportBatchDetailView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)
    template_name = "imports/batch_detail.html"
    paginate_by = 100

    def get(self, request, pk):
        batch = get_object_or_404(ImportBatch, pk=pk)
        rows_queryset = batch.rows.all()

        outcome_filter = request.GET.get("outcome", "").strip()
        if outcome_filter:
            rows_queryset = rows_queryset.filter(outcome=outcome_filter)

        paginator = Paginator(rows_queryset, self.paginate_by)
        page_obj = paginator.get_page(request.GET.get("page"))

        return render(
            request,
            self.template_name,
            {
                "batch": batch,
                "rows": page_obj,
                "page_obj": page_obj,
                "is_paginated": page_obj.has_other_pages(),
                "outcome_filter": outcome_filter,
                "row_outcomes": ImportRowOutcome.choices,
                "override_form": RowLocationOverrideForm(),
                "can_edit": batch.status
                in (ImportBatchStatus.PREVIEWED, ImportBatchStatus.PARTIALLY_COMPLETED),
                "can_execute": batch.status
                in (ImportBatchStatus.PREVIEWED, ImportBatchStatus.PARTIALLY_COMPLETED),
                "is_repeat_of_completed": _is_repeat_of_completed(batch),
            },
        )


class ImportRowOverrideLocationView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk, row_pk):
        batch = get_object_or_404(ImportBatch, pk=pk)
        row = get_object_or_404(ImportRow, pk=row_pk, batch=batch)
        form = RowLocationOverrideForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Select a valid location.")
            return redirect(batch.get_absolute_url())

        try:
            set_row_location_override(
                row=row, location=form.cleaned_data["location"], user=request.user
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Row {row.row_number}: location set.")
        return redirect(batch.get_absolute_url())


class ImportRowSkipView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk, row_pk):
        batch = get_object_or_404(ImportBatch, pk=pk)
        row = get_object_or_404(ImportRow, pk=row_pk, batch=batch)
        try:
            skip_row(row=row, user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Row {row.row_number}: skipped.")
        return redirect(batch.get_absolute_url())


class ImportRowAcknowledgeDuplicateView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk, row_pk):
        batch = get_object_or_404(ImportBatch, pk=pk)
        row = get_object_or_404(ImportRow, pk=row_pk, batch=batch)
        try:
            acknowledge_row_duplicate_serial(row=row, user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Row {row.row_number}: duplicate serial acknowledged.")
        return redirect(batch.get_absolute_url())


class ImportExecuteView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk):
        batch = get_object_or_404(ImportBatch, pk=pk)
        if batch.status not in (ImportBatchStatus.PREVIEWED, ImportBatchStatus.PARTIALLY_COMPLETED):
            messages.error(request, "This batch cannot be executed in its current state.")
            return redirect(batch.get_absolute_url())

        if _is_repeat_of_completed(batch) and request.POST.get("confirm_repeat_upload") != "true":
            messages.error(
                request,
                "A completed import with the same file contents already exists. Confirm below "
                "to proceed, or cancel if this is a mistake.",
            )
            return redirect(batch.get_absolute_url())

        execute_batch(batch=batch, user=request.user)
        messages.success(
            request,
            f"Import finished: {batch.imported_count} imported, {batch.warning_count} still need "
            f"attention, {batch.failed_count} failed, {batch.skipped_count} skipped.",
        )
        return redirect(batch.get_absolute_url())


class ImportResultsDownloadView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def get(self, request, pk):
        batch = get_object_or_404(ImportBatch, pk=pk)
        response = HttpResponse(build_results_csv(batch), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="import_{batch.pk}_results.csv"'
        record_event(
            actor=request.user,
            event_type=AuditEvent.EventType.EXPORT_EXECUTED,
            obj=batch,
            summary=f"Downloaded results for import batch '{batch.source_filename}'",
        )
        return response


class ImportTemplateDownloadView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def get(self, request):
        response = HttpResponse(build_template_csv(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="stock_import_template.csv"'
        return response


class ImportTemplateXlsxDownloadView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def get(self, request):
        response = HttpResponse(
            build_template_xlsx(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="stock_import_template.xlsx"'
        return response
