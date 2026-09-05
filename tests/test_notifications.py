from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.inventory.services.assignments import assign_to_employee
from apps.inventory.services.receipts import receive_stock
from apps.settings.models import (
    NotificationDigestDelivery,
    SystemSettings,
)
from apps.settings.notifications import build_digest, save_notification_subscription


def _configure_mail(settings, administrator):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    administrator.email = "admin@example.com"
    administrator.save(update_fields=["email"])
    smtp = SystemSettings.load()
    smtp.smtp_host = "smtp.example.com"
    smtp.smtp_from_email = "inventory@example.com"
    smtp.updated_by = administrator
    smtp.save()


def _subscription(administrator, country, **overrides):
    values = {
        "recipient": administrator,
        "country": country,
        "is_active": True,
        "notify_low_stock": True,
        "notify_overdue_assignments": True,
        "notify_import_export_failures": True,
        "notify_data_quality": True,
    }
    values.update(overrides)
    return save_notification_subscription(user=administrator, **values)


@pytest.mark.django_db
class TestNotificationSubscriptions:
    def test_administrator_can_create_subscription_with_audit_event(
        self, administrator, location_tree
    ):
        administrator.email = "admin@example.com"
        administrator.save(update_fields=["email"])

        subscription = _subscription(administrator, location_tree["country"])

        assert subscription.created_by == administrator
        assert AuditEvent.objects.filter(object_id=str(subscription.pk)).exists()

    def test_recipient_must_have_access_to_country(
        self, administrator, stock_manager, location_tree
    ):
        stock_manager.email = "manager@example.com"
        stock_manager.save(update_fields=["email"])

        with pytest.raises(ValidationError, match="does not have access"):
            _subscription(administrator, location_tree["country"], recipient=stock_manager)

    def test_only_administrator_can_manage_subscriptions(
        self, client, stock_manager, administrator
    ):
        client.force_login(stock_manager)
        assert client.get(reverse("settings:notifications")).status_code == 403

        client.force_login(administrator)
        assert client.get(reverse("settings:notifications")).status_code == 200


@pytest.mark.django_db
class TestDailyDigests:
    def test_low_stock_digest_is_sent_once_per_day(
        self, settings, mailoutbox, administrator, quantity_product, location_tree
    ):
        _configure_mail(settings, administrator)
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=3,
        )
        _subscription(
            administrator,
            location_tree["country"],
            notify_overdue_assignments=False,
            notify_import_export_failures=False,
            notify_data_quality=False,
        )

        call_command("send_daily_inventory_digest")
        call_command("send_daily_inventory_digest")

        assert len(mailoutbox) == 1
        assert "Low stock (1)" in mailoutbox[0].body
        assert "3 available" in mailoutbox[0].body
        delivery = NotificationDigestDelivery.objects.get()
        assert delivery.status == NotificationDigestDelivery.Status.SENT
        assert delivery.item_counts["low_stock"] == 1

    def test_opted_out_categories_do_not_send_empty_digest(
        self, settings, mailoutbox, administrator, location_tree
    ):
        _configure_mail(settings, administrator)
        _subscription(
            administrator,
            location_tree["country"],
            notify_low_stock=False,
            notify_overdue_assignments=False,
            notify_import_export_failures=False,
            notify_data_quality=False,
        )

        call_command("send_daily_inventory_digest")

        assert mailoutbox == []
        assert NotificationDigestDelivery.objects.get().status == "no_content"

    def test_overdue_digest_excludes_assignments_that_are_not_yet_due(
        self, administrator, unit_product, location_tree
    ):
        administrator.email = "admin@example.com"
        administrator.save(update_fields=["email"])
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="OVERDUE-DIGEST",
        )
        asset = unit_product.unit_assets.get()
        assign_to_employee(
            user=administrator,
            employee_name="Temporary recipient",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            is_temporary_assignment=True,
            expected_return_date=date.today() - timedelta(days=1),
        )
        subscription = _subscription(
            administrator,
            location_tree["country"],
            notify_low_stock=False,
            notify_import_export_failures=False,
            notify_data_quality=False,
        )

        body, counts = build_digest(subscription)

        assert counts["overdue_assignments"] == 1
        assert "Temporary recipient" in body
