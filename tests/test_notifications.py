from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.inventory.services.assignments import assign_to_employee
from apps.inventory.services.receipts import receive_stock
from apps.settings.models import (
    Notification,
    NotificationDigestDelivery,
    SystemSettings,
)
from apps.settings.notifications import (
    build_digest,
    refresh_in_app_notifications,
    save_notification_subscription,
)


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
    def test_manual_refresh_creates_bell_alert_without_sending_digest(
        self, administrator, location_tree, quantity_product
    ):
        _subscription(administrator, location_tree["country"])
        from apps.inventory.services.receipts import receive_stock

        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=1,
        )
        assert refresh_in_app_notifications(user=administrator) == 1
        delivery = NotificationDigestDelivery.objects.get()
        assert delivery.status == NotificationDigestDelivery.Status.PENDING
        assert delivery.sent_at is None
        assert Notification.objects.filter(recipient=administrator, category="low_stock").exists()

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

    def test_editing_a_subscription_survives_the_recipients_deactivation(
        self, client, administrator, stock_manager, location_tree
    ):
        """Regression test: NotificationSubscriptionForm.recipient's
        queryset only ever included active users — fine for creating a new
        subscription, but editing an *existing* one (e.g. simply to turn it
        off) failed Django's "not a valid choice" validation the moment its
        recipient was deactivated, exactly the scenario an Administrator
        most needs to handle (an employee leaves, is deactivated, but their
        standing digest subscription can no longer be turned off).
        """
        from apps.accounts.services import grant_location_access, set_user_active

        stock_manager.email = "manager@example.com"
        stock_manager.save(update_fields=["email"])
        grant_location_access(
            user=stock_manager, location=location_tree["country"], granted_by=administrator
        )
        subscription = _subscription(
            administrator, location_tree["country"], recipient=stock_manager
        )

        set_user_active(user=stock_manager, is_active=False, changed_by=administrator)

        client.force_login(administrator)
        response = client.post(
            reverse("settings:notification_edit", kwargs={"pk": subscription.pk}),
            {
                "recipient": stock_manager.pk,
                "country": location_tree["country"].pk,
                "is_active": "",  # turning it off is exactly the point of this test
                "notify_low_stock": "on",
                "notify_overdue_assignments": "on",
                "notify_import_export_failures": "on",
                "notify_data_quality": "on",
            },
        )
        assert response.status_code == 302
        subscription.refresh_from_db()
        assert subscription.is_active is False


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


@pytest.mark.django_db
class TestInAppNotifications:
    """The bell's backing rows — created alongside (not instead of) the
    email digest, by apps.settings.notifications.sync_in_app_notifications(),
    reusing build_digest()'s own counts rather than re-detecting anything.
    """

    def test_low_stock_creates_an_in_app_notification(
        self, administrator, quantity_product, location_tree
    ):
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

        notification = Notification.objects.get()
        assert notification.recipient == administrator
        assert notification.category == "low_stock"
        assert notification.read_at is None
        assert "low on stock" in notification.summary
        assert notification.url

    def test_rerunning_the_command_does_not_duplicate(
        self, administrator, quantity_product, location_tree
    ):
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

        assert Notification.objects.count() == 1

    def test_no_content_digest_creates_no_notification(self, administrator, location_tree):
        _subscription(
            administrator,
            location_tree["country"],
            notify_low_stock=False,
            notify_overdue_assignments=False,
            notify_import_export_failures=False,
            notify_data_quality=False,
        )

        call_command("send_daily_inventory_digest")

        assert Notification.objects.count() == 0

    def test_open_notification_marks_read_and_redirects(
        self, client, administrator, quantity_product, location_tree
    ):
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
        notification = Notification.objects.get()

        client.force_login(administrator)
        response = client.get(reverse("settings:notification_open", args=[notification.pk]))
        assert response.status_code == 302
        notification.refresh_from_db()
        assert notification.read_at is not None

    def test_cannot_open_another_users_notification(
        self, client, administrator, stock_manager, quantity_product, location_tree
    ):
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
        notification = Notification.objects.get()

        client.force_login(stock_manager)
        response = client.get(reverse("settings:notification_open", args=[notification.pk]))
        assert response.status_code == 404
        notification.refresh_from_db()
        assert notification.read_at is None

    def test_mark_all_read(self, client, administrator, quantity_product, location_tree):
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

        client.force_login(administrator)
        response = client.post(reverse("settings:notification_mark_all_read"))
        assert response.status_code == 302
        assert not Notification.objects.filter(recipient=administrator, read_at__isnull=True)

    def test_bell_context_shows_unread_count_on_any_page(
        self, client, administrator, quantity_product, location_tree
    ):
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

        client.force_login(administrator)
        response = client.get(reverse("core:home"))
        assert response.context["unread_notification_count"] == 1
        assert len(response.context["unread_notifications"]) == 1

    def test_list_view_scoped_to_the_requesting_user(
        self, client, administrator, stock_manager, quantity_product, location_tree
    ):
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

        client.force_login(stock_manager)
        response = client.get(reverse("settings:notification_list"))
        assert response.status_code == 200
        assert list(response.context["notifications"]) == []
