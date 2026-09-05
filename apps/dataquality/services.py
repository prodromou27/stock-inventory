"""apps.dataquality's one write path (besides the correction helpers) —
run_detection() — plus the resolve/dismiss actions the workspace UI calls.

Administrator-only throughout, not Administrator-or-StockManager like most
of this app's other admin surfaces: DataQualityFinding has no location FK
(kept dependency-free by design, see the model's docstring), so it can't be
run through apps.locations.scoping the way every other location-touching
view in this app must be. Rather than invent a parallel, string-matching
scoping mechanism for one small app, this whole workspace is simply
Administrator-only — an Administrator already sees everything everywhere,
so there's no scoping question to answer.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role
from apps.locations.scoping import location_breadcrumb_map

from .checks import ALL_CHECKS
from .models import DataQualityFinding, DataQualityStatus


def _dedup_key(issue_type, object_type, object_id):
    return f"{issue_type}:{object_type}:{object_id}"


@transaction.atomic
def run_detection(*, user=None):
    """Runs every check in apps.dataquality.checks.ALL_CHECKS and upserts
    DataQualityFinding rows by dedup_key. `user=None` is the management-
    command path (a scheduled/manual `manage.py run_data_quality_scan`,
    mirroring apps.exports' cron-driven pattern); a real user is the
    workspace's "Refresh scan" button, which does require the role check
    below — the command's caller is trusted by construction (whoever can
    run `manage.py` on the box already has full database access).

    Re-running is idempotent and additive, never destructive: a
    still-detected OPEN finding has its descriptive fields refreshed in
    place (detected_at/status untouched); a no-longer-detected OPEN finding
    auto-resolves; a DISMISSED finding is never touched either way, per the
    original request's explicit "must NOT auto-change stock/history" and
    "resolution status" being an operator's call, not the scanner's to
    overturn. A RESOLVED finding that gets detected again is reopened — the
    same real-world problem has recurred, which is meaningfully different
    from "still present the whole time."
    """
    if user is not None:
        require_role(user, ADMINISTRATOR)

    breadcrumbs = location_breadcrumb_map()
    detected_keys = set()

    for check in ALL_CHECKS:
        for finding in check(breadcrumbs):
            dedup_key = _dedup_key(
                finding["issue_type"], finding["object_type"], finding["object_id"]
            )
            detected_keys.add(dedup_key)
            _upsert_finding(dedup_key, finding)

    reopened = _reopen_recurring_resolved(detected_keys)
    auto_resolved = _auto_resolve_undetected(detected_keys)

    return {
        "open_count": DataQualityFinding.objects.filter(status=DataQualityStatus.OPEN).count(),
        "detected_count": len(detected_keys),
        "reopened_count": reopened,
        "auto_resolved_count": auto_resolved,
    }


def _upsert_finding(dedup_key, finding):
    existing = DataQualityFinding.objects.filter(dedup_key=dedup_key).first()
    if existing is not None and existing.status == DataQualityStatus.DISMISSED:
        return  # dismissed findings are never touched by detection
    if existing is not None and existing.status == DataQualityStatus.OPEN:
        # Still open, still detected — refresh descriptive fields only.
        DataQualityFinding.objects.filter(pk=existing.pk).update(
            severity=finding["severity"],
            country=finding["country"],
            location_label=finding["location_label"],
            explanation=finding["explanation"],
            recommended_correction=finding["recommended_correction"],
        )
        return
    if existing is not None and existing.status == DataQualityStatus.RESOLVED:
        return  # handled by _reopen_recurring_resolved() below
    DataQualityFinding.objects.create(dedup_key=dedup_key, **finding)


def _reopen_recurring_resolved(detected_keys):
    resolved_but_detected = DataQualityFinding.objects.filter(
        dedup_key__in=detected_keys, status=DataQualityStatus.RESOLVED
    )
    count = resolved_but_detected.count()
    if count:
        resolved_but_detected.update(
            status=DataQualityStatus.OPEN, resolved_at=None, resolved_by=None, resolution_note=""
        )
    return count


def _auto_resolve_undetected(detected_keys):
    undetected_open = DataQualityFinding.objects.filter(status=DataQualityStatus.OPEN).exclude(
        dedup_key__in=detected_keys
    )
    count = undetected_open.count()
    if count:
        undetected_open.update(
            status=DataQualityStatus.RESOLVED,
            resolved_at=timezone.now(),
            resolved_by=None,
            resolution_note="No longer detected — automatically resolved by rescan.",
        )
    return count


@transaction.atomic
def resolve_finding(*, finding, user, resolution_note=""):
    """Manual "Mark Resolved" — for issue types with no safe automated
    correction (duplicate serial/product, invalid hierarchy, orphaned
    reference): the operator fixes the underlying record through its own
    normal edit page, then comes back here to close the finding out.
    """
    require_role(user, ADMINISTRATOR)
    if finding.status != DataQualityStatus.OPEN:
        raise ValidationError("Only an open finding can be resolved.")

    finding.status = DataQualityStatus.RESOLVED
    finding.resolved_at = timezone.now()
    finding.resolved_by = user
    finding.resolution_note = resolution_note
    finding.save(update_fields=["status", "resolved_at", "resolved_by", "resolution_note"])

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.DATA_QUALITY_FINDING_RESOLVED,
        obj=finding,
        summary=f"Resolved data quality finding: {finding}",
        new_values={"resolution_note": resolution_note},
    )
    return finding


@transaction.atomic
def dismiss_finding(*, finding, user, resolution_note=""):
    """A finding that's a known, accepted non-issue (a legitimate near-
    duplicate product, say) — dismissed findings are excluded from the
    default workspace view and never reopened or re-touched by a later
    scan, unlike a resolved one (which reopens if the same issue recurs).
    """
    require_role(user, ADMINISTRATOR)
    if finding.status == DataQualityStatus.DISMISSED:
        raise ValidationError("This finding is already dismissed.")

    finding.status = DataQualityStatus.DISMISSED
    finding.resolved_at = timezone.now()
    finding.resolved_by = user
    finding.resolution_note = resolution_note
    finding.save(update_fields=["status", "resolved_at", "resolved_by", "resolution_note"])

    record_event(
        actor=user,
        event_type=AuditEvent.EventType.DATA_QUALITY_FINDING_RESOLVED,
        obj=finding,
        summary=f"Dismissed data quality finding: {finding}",
        new_values={"resolution_note": resolution_note},
    )
    return finding
