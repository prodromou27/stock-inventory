from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import record_event
from apps.core.authorization import ADMINISTRATOR, require_role
from apps.dataquality.models import (
    DataQualityFinding,
    DataQualitySeverity,
    DataQualityStatus,
)
from apps.exports.models import ExportRunStatus, ExportSettings
from apps.imports.models import ImportBatch, ImportBatchStatus
from apps.inventory.access import scope_transaction_queryset
from apps.inventory.models import InventoryTransaction, MovementType
from apps.inventory.services.returns import outstanding_quantity_lines
from apps.locations.models import LocationLevel
from apps.locations.scoping import require_location_access
from apps.reporting.queries import low_stock_balances

from .models import Notification, NotificationDigestDelivery, NotificationSubscription
from .services import send_configured_email


@transaction.atomic
def save_notification_subscription(*, user, subscription=None, **values):
    require_role(user, ADMINISTRATOR)
    subscription = subscription or NotificationSubscription(created_by=user)
    country = values["country"]
    recipient = values["recipient"]
    if country.level != LocationLevel.COUNTRY:
        raise ValidationError("Notification subscriptions must be assigned to a country.")
    try:
        require_location_access(recipient, country)
    except PermissionDenied as exc:
        raise ValidationError(
            "The recipient does not have access to the selected country."
        ) from exc

    old_values = (
        {
            field: getattr(subscription, field)
            for field in (
                "recipient_id",
                "country_id",
                "is_active",
                "notify_low_stock",
                "notify_overdue_assignments",
                "notify_import_export_failures",
                "notify_data_quality",
            )
        }
        if subscription.pk
        else {}
    )
    for field, value in values.items():
        setattr(subscription, field, value)
    subscription.updated_by = user
    subscription.full_clean()
    subscription.save()
    record_event(
        actor=user,
        event_type=AuditEvent.EventType.RECORD_UPDATED,
        obj=subscription,
        summary=f"Saved notification subscription for {recipient} in {country}",
        old_values=old_values,
        new_values={
            "recipient_id": recipient.pk,
            "country_id": str(country.pk),
            **{
                field: getattr(subscription, field)
                for field in (
                    "is_active",
                    "notify_low_stock",
                    "notify_overdue_assignments",
                    "notify_import_export_failures",
                    "notify_data_quality",
                )
            },
        },
    )
    return subscription


def _overdue_assignments(subscription, today):
    queryset = scope_transaction_queryset(
        subscription.recipient,
        InventoryTransaction.objects.filter(
            movement_type=MovementType.ASSIGNMENT,
            is_temporary_assignment=True,
            expected_return_date__lt=today,
            lines__from_location__path__descendant_or_self=subscription.country.path,
        ).select_related("performed_by"),
    ).order_by("expected_return_date", "transaction_number")
    outstanding = []
    for assignment in queryset:
        has_units = assignment.lines.filter(
            unit_asset__current_custody_transaction=assignment
        ).exists()
        if has_units or outstanding_quantity_lines(assignment):
            outstanding.append(assignment)
    return outstanding


def build_digest(subscription, *, today=None):
    today = today or timezone.localdate()
    since = timezone.now() - timedelta(days=1)
    sections = []
    counts = {}

    if subscription.notify_low_stock:
        rows = list(low_stock_balances(subscription.recipient, location=subscription.country))
        counts["low_stock"] = len(rows)
        if rows:
            lines = [
                f"- {row.product}: {row.available} available at {row.location} "
                f"(threshold {row.product.low_stock_threshold})"
                for row in rows[:50]
            ]
            sections.append(("Low stock", lines, len(rows)))

    if subscription.notify_overdue_assignments:
        rows = _overdue_assignments(subscription, today)
        counts["overdue_assignments"] = len(rows)
        if rows:
            lines = [
                f"- {row.transaction_number}: {row.employee_name} — due "
                f"{row.expected_return_date.isoformat()}"
                for row in rows[:50]
            ]
            sections.append(("Overdue temporary assignments", lines, len(rows)))

    if subscription.notify_import_export_failures:
        imports = list(
            ImportBatch.objects.filter(
                status__in=(ImportBatchStatus.FAILED, ImportBatchStatus.PARTIALLY_COMPLETED),
                updated_at__gte=since,
                default_location__path__descendant_or_self=subscription.country.path,
            ).order_by("-updated_at")
        )
        export_settings = ExportSettings.objects.filter(
            pk=1, last_run_status=ExportRunStatus.FAILED, last_run_at__gte=since
        ).first()
        failure_count = len(imports) + int(export_settings is not None)
        counts["import_export_failures"] = failure_count
        if failure_count:
            lines = [
                f"- Import {batch.source_filename}: {batch.get_status_display()}"
                for batch in imports
            ]
            if export_settings:
                lines.append(f"- Scheduled export: {export_settings.last_run_detail or 'Failed'}")
            sections.append(
                ("Import/export failures in the last 24 hours", lines[:50], failure_count)
            )

    if subscription.notify_data_quality:
        findings = list(
            DataQualityFinding.objects.filter(
                status=DataQualityStatus.OPEN,
                severity=DataQualitySeverity.HIGH,
                country=subscription.country.name,
            ).order_by("-detected_at")
        )
        counts["high_data_quality"] = len(findings)
        if findings:
            lines = [
                f"- {finding.get_issue_type_display()}: {finding.explanation}"
                for finding in findings[:50]
            ]
            sections.append(
                ("Unresolved high-severity data-quality findings", lines, len(findings))
            )

    if not sections:
        return "", counts

    body = [
        f"Stock Inventory daily digest for {subscription.country.name}",
        f"Date: {today.isoformat()}",
    ]
    for heading, lines, total in sections:
        body.extend(["", f"{heading} ({total})", *lines])
        if total > len(lines):
            body.append(f"- …and {total - len(lines)} more")
    body.extend(["", "Sign in to Stock Inventory to review and resolve these items."])
    return "\n".join(body), counts


def _notification_summary_and_url(category, count, country):
    """The in-app bell's summary text and link-through page for one
    build_digest() `counts` key — reuses build_digest()'s own detection/
    counting instead of re-querying low_stock_balances()/_overdue_assignments()/
    etc a second time just to decide whether to raise a bell notification.
    """
    if category == "low_stock":
        return (
            f"{count} product(s) low on stock in {country.name}",
            f"{reverse('reporting:low_stock')}?location={country.pk}",
        )
    if category == "overdue_assignments":
        return (
            f"{count} overdue temporary assignment(s) in {country.name}",
            reverse("inventory:transaction_list"),
        )
    if category == "import_export_failures":
        return (
            f"{count} import/export failure(s) in the last 24 hours",
            reverse("imports:batch_list"),
        )
    if category == "high_data_quality":
        return (
            f"{count} unresolved high-severity data-quality finding(s) in {country.name}",
            reverse("dataquality:workspace"),
        )
    return (f"{count} item(s) need attention in {country.name}", "")


def sync_in_app_notifications(*, delivery, counts):
    """One Notification row per truthy build_digest() count, upserted
    against this delivery (the (subscription, digest_date) row already
    idempotent per send_daily_digests()'s own get_or_create) — re-running
    the daily command is safe and never duplicates a bell entry, and a
    count that's since dropped to zero on a retry is removed rather than
    left showing a stale alert.
    """
    subscription = delivery.subscription
    current_categories = set()
    for category, count in counts.items():
        if not count:
            continue
        current_categories.add(category)
        summary, url = _notification_summary_and_url(category, count, subscription.country)
        Notification.objects.update_or_create(
            delivery=delivery,
            category=category,
            defaults={
                "recipient": subscription.recipient,
                "country": subscription.country,
                "summary": summary,
                "url": url,
            },
        )
    delivery.notifications.exclude(category__in=current_categories).delete()


def send_daily_digests(*, today=None):
    today = today or timezone.localdate()
    results = {"sent": 0, "no_content": 0, "failed": 0, "skipped": 0}
    subscriptions = NotificationSubscription.objects.filter(
        is_active=True, recipient__is_active=True
    ).select_related("recipient", "country")
    for subscription in subscriptions:
        delivery, created = NotificationDigestDelivery.objects.get_or_create(
            subscription=subscription,
            digest_date=today,
            defaults={"status": NotificationDigestDelivery.Status.FAILED},
        )
        if not created and delivery.status in (
            NotificationDigestDelivery.Status.SENT,
            NotificationDigestDelivery.Status.NO_CONTENT,
        ):
            results["skipped"] += 1
            continue

        body, counts = build_digest(subscription, today=today)
        delivery.item_counts = counts
        # In-app bell notifications are independent of email delivery
        # succeeding — a low-stock alert is still worth surfacing even if
        # SMTP is down — so this runs regardless of which branch below
        # follows, including "no content" (which correctly clears any
        # stale notification from a previous run whose count has since
        # dropped to zero).
        sync_in_app_notifications(delivery=delivery, counts=counts)
        if not body:
            delivery.status = NotificationDigestDelivery.Status.NO_CONTENT
            delivery.detail = "No matching alerts."
            delivery.sent_at = None
            delivery.save(
                update_fields=["item_counts", "status", "detail", "sent_at", "updated_at"]
            )
            results["no_content"] += 1
            continue
        try:
            send_configured_email(
                recipient=subscription.recipient.email,
                subject=f"Stock Inventory daily digest — {subscription.country.name}",
                body=body,
            )
        except Exception as exc:
            delivery.status = NotificationDigestDelivery.Status.FAILED
            delivery.detail = str(exc)[:2000]
            delivery.sent_at = None
            results["failed"] += 1
        else:
            delivery.status = NotificationDigestDelivery.Status.SENT
            delivery.detail = ""
            delivery.sent_at = timezone.now()
            results["sent"] += 1
        delivery.save(update_fields=["item_counts", "status", "detail", "sent_at", "updated_at"])
    return results
