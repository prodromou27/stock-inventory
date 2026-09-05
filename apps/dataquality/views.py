from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.authorization import ADMINISTRATOR, RoleRequiredMixin
from apps.core.csv_export import CSVExportMixin
from apps.inventory.services.corrections import correct_reference_fields

from .forms import ReferenceCorrectionForm, ResolutionForm
from .models import DataQualityFinding, DataQualityIssueType, DataQualitySeverity, DataQualityStatus
from .services import dismiss_finding, resolve_finding, run_detection

# issue_type -> where "Correct" should send the operator. Types not listed
# here have no safe automated correction (duplicate serial/product, invalid
# hierarchy, orphaned reference, missing procurement info) — those get a
# "View record" link (built the same way, see _object_url()) and a manual
# Resolve/Dismiss only, per the original request's explicit list of which
# findings may be auto-corrected at all.
_CORRECTION_URL_NAMES = {
    DataQualityIssueType.MISSING_LOCATION: "inventory:asset_correct",
    DataQualityIssueType.MISSING_CUSTODIAN: "inventory:asset_correct",
    DataQualityIssueType.STALE_CUSTODY_POINTER: "inventory:asset_correct",
    DataQualityIssueType.INVALID_BALANCE: "inventory:balance_correct",
    DataQualityIssueType.CUSTOMER_STOCK_MISSING_REFERENCE: "dataquality:correct_finding",
}

# object_type -> reverse() name, both keyed the same way DataQualityFinding.
# object_type is written (see apps.dataquality.checks) — a direct link to
# go look at the record itself, independent of whether it's correctable.
_OBJECT_URL_NAMES = {
    "UnitAsset": "inventory:asset_detail",
    "StockBalance": "inventory:balance_detail",
    "Product": "catalog:product_detail",
    "Location": "locations:detail",
}


def _object_url(finding):
    url_name = _OBJECT_URL_NAMES.get(finding.object_type)
    if not url_name:
        return ""
    try:
        return reverse(url_name, kwargs={"pk": finding.object_id})
    except Exception:  # a stale/malformed object_id just gets no link
        return ""


def _correction_url(finding):
    url_name = _CORRECTION_URL_NAMES.get(finding.issue_type)
    if not url_name:
        return ""
    if url_name == "dataquality:correct_finding":
        return reverse(url_name, kwargs={"pk": finding.pk})
    try:
        return f"{reverse(url_name, kwargs={'pk': finding.object_id})}?finding={finding.pk}"
    except Exception:  # noqa: BLE001 — same as _object_url() above
        return ""


class DataQualityWorkspaceView(LoginRequiredMixin, RoleRequiredMixin, CSVExportMixin, ListView):
    """The whole centre is Administrator-only — see apps.dataquality.
    services' module docstring for why (no location FK to scope by).
    Defaults to showing only OPEN findings; `status=all` (or `resolved`/
    `dismissed`) shows the rest — resolved/dismissed findings are never the
    first thing an operator needs to act on.
    """

    allowed_roles = (ADMINISTRATOR,)
    model = DataQualityFinding
    template_name = "dataquality/workspace.html"
    context_object_name = "findings"
    paginate_by = 50
    csv_filename = "data_quality_findings.csv"
    csv_headers = [
        "Severity",
        "Issue Type",
        "Object Type",
        "Object ID",
        "Country",
        "Location",
        "Status",
        "Detected",
        "Resolved",
        "Explanation",
        "Recommended Correction",
    ]

    def get_queryset(self):
        queryset = DataQualityFinding.objects.all()
        status = self.request.GET.get("status") or DataQualityStatus.OPEN
        if status != "all":
            queryset = queryset.filter(status=status)
        if severity := self.request.GET.get("severity"):
            queryset = queryset.filter(severity=severity)
        if issue_type := self.request.GET.get("issue_type"):
            queryset = queryset.filter(issue_type=issue_type)
        if country := self.request.GET.get("country"):
            queryset = queryset.filter(country=country)
        return queryset

    def csv_rows(self, queryset):
        for finding in queryset:
            yield [
                finding.get_severity_display(),
                finding.get_issue_type_display(),
                finding.object_type,
                finding.object_id,
                finding.country,
                finding.location_label,
                finding.get_status_display(),
                finding.detected_at.isoformat(),
                finding.resolved_at.isoformat() if finding.resolved_at else "",
                finding.explanation,
                finding.recommended_correction,
            ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["counts"] = {
            "open": DataQualityFinding.objects.filter(status=DataQualityStatus.OPEN).count(),
            "resolved": DataQualityFinding.objects.filter(
                status=DataQualityStatus.RESOLVED
            ).count(),
            "dismissed": DataQualityFinding.objects.filter(
                status=DataQualityStatus.DISMISSED
            ).count(),
        }
        context["severities"] = DataQualitySeverity.choices
        context["issue_types"] = DataQualityIssueType.choices
        context["countries"] = (
            DataQualityFinding.objects.exclude(country="")
            .values_list("country", flat=True)
            .distinct()
            .order_by("country")
        )
        context["filters"] = self.request.GET
        context["selected_status"] = self.request.GET.get("status") or DataQualityStatus.OPEN
        # Decorates each in-memory finding with its display-only links —
        # never persisted, just convenient for the template to read as a
        # plain attribute instead of needing a dict-by-pk template filter.
        for finding in context["findings"]:
            finding.object_url = _object_url(finding)
            finding.correction_url = _correction_url(finding)
        return context


class RunDetectionView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request):
        result = run_detection(user=request.user)
        messages.success(
            request,
            f"Scan complete — {result['open_count']} open finding(s); "
            f"{result['auto_resolved_count']} auto-resolved, "
            f"{result['reopened_count']} reopened.",
        )
        return redirect("dataquality:workspace")


class ResolveFindingView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk):
        finding = get_object_or_404(DataQualityFinding, pk=pk)
        form = ResolutionForm(request.POST)
        note = form.data.get("resolution_note", "") if form.is_valid() else ""
        try:
            resolve_finding(finding=finding, user=request.user, resolution_note=note)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Finding resolved.")
        return redirect("dataquality:workspace")


class DismissFindingView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = (ADMINISTRATOR,)

    def post(self, request, pk):
        finding = get_object_or_404(DataQualityFinding, pk=pk)
        form = ResolutionForm(request.POST)
        note = form.data.get("resolution_note", "") if form.is_valid() else ""
        try:
            dismiss_finding(finding=finding, user=request.user, resolution_note=note)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Finding dismissed.")
        return redirect("dataquality:workspace")


class CorrectFindingView(LoginRequiredMixin, RoleRequiredMixin, View):
    """The one finding type with no pre-existing admin-correction screen to
    link out to — see _CORRECTION_URL_NAMES's docstring note. Resolves the
    finding in the same request as the correction, like every other
    correction path this app wires up (apps.inventory.views.
    _resolve_linked_finding for the others).
    """

    allowed_roles = (ADMINISTRATOR,)
    template_name = "dataquality/correct_reference_fields.html"

    def get(self, request, pk):
        finding = get_object_or_404(
            DataQualityFinding,
            pk=pk,
            issue_type=DataQualityIssueType.CUSTOMER_STOCK_MISSING_REFERENCE,
        )
        form = ReferenceCorrectionForm()
        return render(request, self.template_name, {"form": form, "finding": finding})

    def post(self, request, pk):
        from apps.inventory.models import UnitAsset

        finding = get_object_or_404(
            DataQualityFinding,
            pk=pk,
            issue_type=DataQualityIssueType.CUSTOMER_STOCK_MISSING_REFERENCE,
        )
        form = ReferenceCorrectionForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "finding": finding})

        asset = get_object_or_404(UnitAsset, pk=finding.object_id)
        data = form.cleaned_data
        try:
            correct_reference_fields(
                user=request.user,
                unit_asset=asset,
                reason=data["reason"],
                project_reference=data["project_reference"] or None,
                final_customer=data["final_customer"] or None,
            )
            resolve_finding(finding=finding, user=request.user, resolution_note=data["reason"])
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(request, self.template_name, {"form": form, "finding": finding})

        messages.success(request, "Correction applied and finding resolved.")
        return redirect("dataquality:workspace")
