from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.catalog.models import ItemCategory
from apps.catalog.services import create_product
from apps.dataquality.models import DataQualityFinding, DataQualityStatus
from apps.dataquality.services import dismiss_finding, resolve_finding, run_detection
from apps.inventory.models import Condition, StockPurpose, UnitAsset, UnitStatus
from apps.inventory.services.corrections import correct_reference_fields
from apps.inventory.services.receipts import receive_stock


@pytest.fixture
def component_product(administrator):
    return create_product(
        user=administrator,
        brand_name="Crucial",
        model="RAM-DQ",
        product_type_name="Memory Module",
        category=ItemCategory.SERIALIZED_ASSET,
    )


@pytest.mark.django_db
class TestCheckDuplicateSerial:
    def test_finds_both_sides_of_a_duplicate(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DUP",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DUP",
            duplicate_serial_acknowledged=True,
        )
        run_detection(user=None)
        findings = DataQualityFinding.objects.filter(issue_type="duplicate_serial")
        assert findings.count() == 2

    def test_unique_serials_produce_no_finding(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-UNIQUE-DQ",
        )
        run_detection(user=None)
        assert not DataQualityFinding.objects.filter(issue_type="duplicate_serial").exists()


@pytest.mark.django_db
class TestCheckMissingLocationAndCustodian:
    def test_missing_location_on_an_in_stock_asset(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-NOLOC",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-NOLOC")
        UnitAsset.objects.filter(pk=asset.pk).update(current_location=None)
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(
            issue_type="missing_location", object_id=str(asset.pk)
        ).exists()

    def test_assigned_asset_with_no_location_is_not_flagged(
        self, administrator, unit_product, location_tree
    ):
        """Assigned/Delivered/Lost/Disposed legitimately have no location —
        see apps.dataquality.checks._STATUSES_WITHOUT_LOCATION.
        """
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-ASSIGNED-NOLOC",
        )
        from apps.inventory.services.assignments import assign_to_employee

        asset = UnitAsset.objects.get(vendor_serial="SN-ASSIGNED-NOLOC")
        assign_to_employee(
            user=administrator,
            employee_name="Jane",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        run_detection(user=None)
        assert not DataQualityFinding.objects.filter(
            issue_type="missing_location", object_id=str(asset.pk)
        ).exists()

    def test_missing_custodian_on_an_assigned_asset(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-NOCUST",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-NOCUST")
        UnitAsset.objects.filter(pk=asset.pk).update(
            status=UnitStatus.ASSIGNED, current_location=None
        )
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(
            issue_type="missing_custodian", object_id=str(asset.pk)
        ).exists()


@pytest.mark.django_db
class TestCheckSerializedAssetWithoutSerial:
    def test_flags_blank_serial_on_a_serialized_asset(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="",
        )
        asset = UnitAsset.objects.filter(product=unit_product, vendor_serial="").first()
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(
            issue_type="serialized_asset_without_serial", object_id=str(asset.pk)
        ).exists()

    def test_component_category_with_blank_serial_is_not_flagged(
        self, administrator, component_product, location_tree
    ):
        """Only Serialized Asset is expected to always carry a real serial —
        Component/Reusable Accessory items are explicitly allowed to stay
        blank-serial, distinguished by DB id (this session's Add Stock
        rework), so this check must not contradict that.
        """
        from apps.catalog.services import update_product

        update_product(
            product=component_product,
            user=administrator,
            brand_name=component_product.brand.name,
            model=component_product.model,
            product_type_name=component_product.product_type.name,
            category=ItemCategory.COMPONENT,
        )
        receive_stock(
            user=administrator,
            product=component_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="",
        )
        run_detection(user=None)
        assert not DataQualityFinding.objects.filter(
            issue_type="serialized_asset_without_serial"
        ).exists()


@pytest.mark.django_db
class TestCheckInvalidBalance:
    def test_flags_stock_for_a_deactivated_product(
        self, administrator, quantity_product, location_tree
    ):
        from apps.catalog.services import update_product

        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=5,
        )
        update_product(
            product=quantity_product,
            user=administrator,
            brand_name=quantity_product.brand.name,
            model=quantity_product.model,
            product_type_name=quantity_product.product_type.name,
            category=quantity_product.category,
            is_active=False,
        )
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(issue_type="invalid_balance").exists()


@pytest.mark.django_db
class TestCheckStaleCustodyPointer:
    def test_flags_a_custody_pointer_left_after_return(
        self, administrator, unit_product, location_tree
    ):
        from apps.inventory.services.assignments import assign_to_employee
        from apps.inventory.services.returns import return_stock

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-STALE-CUSTODY",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-STALE-CUSTODY")
        assignment_txn = assign_to_employee(
            user=administrator,
            employee_name="Jane",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        return_stock(
            user=administrator,
            location=location_tree["room"],
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            original_transaction=assignment_txn,
        )
        # Simulate a stale pointer left behind by corrupting the row directly
        # (write_unit_line() itself always clears this correctly on return —
        # this check exists for exactly the kind of drift that shouldn't
        # happen through normal code paths).
        asset.refresh_from_db()
        UnitAsset.objects.filter(pk=asset.pk).update(
            current_custody_transaction_id=asset.transaction_lines.first().transaction_id
        )
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(
            issue_type="stale_custody_pointer", object_id=str(asset.pk)
        ).exists()


@pytest.mark.django_db
class TestCheckCustomerStockMissingReference:
    def test_flags_customer_stock_with_no_reference(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CUST-NOREF",
            stock_purpose=StockPurpose.CUSTOMER,
            final_customer="Acme",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CUST-NOREF")
        UnitAsset.objects.filter(pk=asset.pk).update(final_customer="", project_reference="")
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(
            issue_type="customer_stock_missing_reference", object_id=str(asset.pk)
        ).exists()

    def test_internal_stock_is_not_flagged(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-INTERNAL-NOREF",
        )
        run_detection(user=None)
        assert not DataQualityFinding.objects.filter(
            issue_type="customer_stock_missing_reference"
        ).exists()


@pytest.mark.django_db
class TestCheckInactiveLocationWithActiveStock:
    def test_flags_an_in_stock_asset_at_a_deactivated_location(
        self, administrator, unit_product, location_tree
    ):
        from apps.locations.services import deactivate_location

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-INACTIVE-LOC",
        )
        deactivate_location(location=location_tree["room"], user=administrator)
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(
            issue_type="inactive_location_with_active_stock", object_type="UnitAsset"
        ).exists()


@pytest.mark.django_db
class TestCheckMissingProcurementInfo:
    def test_flags_a_new_unit_with_no_supplier_or_invoice(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-NEW-NOPROC",
            condition=Condition.NEW,
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-NEW-NOPROC")
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(
            issue_type="missing_procurement_info", object_id=str(asset.pk)
        ).exists()

    def test_used_condition_is_not_flagged(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-USED-NOPROC",
            condition=Condition.USED,
        )
        run_detection(user=None)
        assert not DataQualityFinding.objects.filter(issue_type="missing_procurement_info").exists()


@pytest.mark.django_db
class TestCheckDuplicateProduct:
    def test_flags_same_brand_and_model(self, administrator):
        create_product(
            user=administrator,
            brand_name="Acme",
            model="Widget",
            product_type_name="Gadget",
            category=ItemCategory.SERIALIZED_ASSET,
        )
        create_product(
            user=administrator,
            brand_name="Acme",
            model="Widget",
            product_type_name="Gadget",
            category=ItemCategory.SERIALIZED_ASSET,
            duplicate_acknowledged=True,
        )
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(issue_type="duplicate_product").count() == 2


@pytest.mark.django_db
class TestCheckOrphanedTransactionReference:
    def test_flags_a_live_status_that_diverges_from_the_ledger(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DIVERGED",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DIVERGED")
        # Ledger's last line says In Stock — corrupt only the live
        # denormalized field, simulating drift write_unit_line() itself
        # never produces.
        UnitAsset.objects.filter(pk=asset.pk).update(status=UnitStatus.DAMAGED)
        run_detection(user=None)
        assert DataQualityFinding.objects.filter(
            issue_type="orphaned_transaction_reference", object_id=str(asset.pk)
        ).exists()

    def test_consistent_asset_is_not_flagged(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CONSISTENT",
        )
        run_detection(user=None)
        assert not DataQualityFinding.objects.filter(
            issue_type="orphaned_transaction_reference"
        ).exists()


@pytest.mark.django_db
class TestRunDetectionLifecycle:
    def test_requires_administrator_when_a_user_is_given(self, stock_manager):
        with pytest.raises(PermissionDenied):
            run_detection(user=stock_manager)

    def test_management_command_path_needs_no_user(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-NOUSER",
        )
        result = run_detection(user=None)  # must not raise
        assert isinstance(result, dict)

    def test_dismissed_finding_is_never_touched_by_a_rescan(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DISMISS-ME",
            condition=Condition.NEW,
        )
        run_detection(user=None)
        finding = DataQualityFinding.objects.get(
            issue_type="missing_procurement_info", object_id__isnull=False
        )
        dismiss_finding(finding=finding, user=administrator)
        run_detection(user=None)
        finding.refresh_from_db()
        assert finding.status == DataQualityStatus.DISMISSED

    def test_no_longer_detected_open_finding_auto_resolves(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-AUTORESOLVE",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-AUTORESOLVE")
        UnitAsset.objects.filter(pk=asset.pk).update(current_location=None)
        run_detection(user=None)
        finding = DataQualityFinding.objects.get(
            issue_type="missing_location", object_id=str(asset.pk)
        )
        assert finding.status == DataQualityStatus.OPEN

        UnitAsset.objects.filter(pk=asset.pk).update(current_location=location_tree["room"])
        run_detection(user=None)
        finding.refresh_from_db()
        assert finding.status == DataQualityStatus.RESOLVED
        assert finding.resolved_by is None
        assert "automatically resolved" in finding.resolution_note

    def test_resolved_finding_reopens_if_it_recurs(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-RECUR",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-RECUR")
        UnitAsset.objects.filter(pk=asset.pk).update(current_location=None)
        run_detection(user=None)
        finding = DataQualityFinding.objects.get(
            issue_type="missing_location", object_id=str(asset.pk)
        )
        resolve_finding(finding=finding, user=administrator, resolution_note="fixed manually")
        UnitAsset.objects.filter(pk=asset.pk).update(current_location=None)
        run_detection(user=None)
        finding.refresh_from_db()
        assert finding.status == DataQualityStatus.OPEN
        assert finding.resolved_at is None


@pytest.mark.django_db
class TestResolveAndDismissFinding:
    def test_resolve_requires_administrator(self, stock_manager, administrator):
        finding = DataQualityFinding.objects.create(
            dedup_key="x:UnitAsset:1",
            issue_type="missing_location",
            severity="high",
            object_type="UnitAsset",
            object_id="1",
            explanation="test",
        )
        with pytest.raises(PermissionDenied):
            resolve_finding(finding=finding, user=stock_manager)

    def test_resolving_an_already_resolved_finding_is_rejected(self, administrator):
        finding = DataQualityFinding.objects.create(
            dedup_key="x:UnitAsset:2",
            issue_type="missing_location",
            severity="high",
            status=DataQualityStatus.RESOLVED,
            object_type="UnitAsset",
            object_id="2",
            explanation="test",
        )
        with pytest.raises(ValidationError):
            resolve_finding(finding=finding, user=administrator)

    def test_resolve_records_an_audit_event(self, administrator):
        finding = DataQualityFinding.objects.create(
            dedup_key="x:UnitAsset:3",
            issue_type="missing_location",
            severity="high",
            object_type="UnitAsset",
            object_id="3",
            explanation="test",
        )
        resolve_finding(finding=finding, user=administrator, resolution_note="fixed")
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.DATA_QUALITY_FINDING_RESOLVED
        ).exists()

    def test_dismissing_an_already_dismissed_finding_is_rejected(self, administrator):
        finding = DataQualityFinding.objects.create(
            dedup_key="x:UnitAsset:4",
            issue_type="missing_location",
            severity="high",
            status=DataQualityStatus.DISMISSED,
            object_type="UnitAsset",
            object_id="4",
            explanation="test",
        )
        with pytest.raises(ValidationError):
            dismiss_finding(finding=finding, user=administrator)


@pytest.mark.django_db
class TestCorrectReferenceFields:
    def test_updates_the_fields_and_records_a_correction(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REFCORRECT",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REFCORRECT")
        correct_reference_fields(
            user=administrator,
            unit_asset=asset,
            reason="found the missing customer",
            final_customer="Acme",
            project_reference="PRJ-1",
        )
        asset.refresh_from_db()
        assert asset.final_customer == "Acme"
        assert asset.project_reference == "PRJ-1"
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.ADMIN_CORRECTION,
            summary__icontains="reference fields updated",
        ).exists()

    def test_requires_a_reason(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REFCORRECT-NOREASON",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REFCORRECT-NOREASON")
        with pytest.raises(ValidationError):
            correct_reference_fields(
                user=administrator, unit_asset=asset, reason="", final_customer="Acme"
            )

    def test_requires_administrator(
        self, stock_manager, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-REFCORRECT-NOTADMIN",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-REFCORRECT-NOTADMIN")
        with pytest.raises(PermissionDenied):
            correct_reference_fields(
                user=stock_manager, unit_asset=asset, reason="x", final_customer="Acme"
            )


@pytest.mark.django_db
class TestDataQualityWorkspaceView:
    def test_administrator_can_view(self, client, administrator):
        client.force_login(administrator)
        assert client.get(reverse("dataquality:workspace")).status_code == 200

    def test_stock_manager_forbidden(self, client, stock_manager_with_room_access):
        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("dataquality:workspace"))
        assert response.status_code == 403

    def test_read_only_forbidden(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("dataquality:workspace"))
        assert response.status_code == 403

    def test_anonymous_redirected(self, client):
        response = client.get(reverse("dataquality:workspace"))
        assert response.status_code == 302

    def test_defaults_to_open_only(self, client, administrator):
        DataQualityFinding.objects.create(
            dedup_key="a",
            issue_type="missing_location",
            severity="high",
            status=DataQualityStatus.RESOLVED,
            object_type="UnitAsset",
            object_id="1",
            explanation="resolved one",
        )
        DataQualityFinding.objects.create(
            dedup_key="b",
            issue_type="missing_location",
            severity="high",
            status=DataQualityStatus.OPEN,
            object_type="UnitAsset",
            object_id="2",
            explanation="open one",
        )
        client.force_login(administrator)
        response = client.get(reverse("dataquality:workspace"))
        findings = list(response.context["findings"])
        assert len(findings) == 1
        assert findings[0].explanation == "open one"

    def test_status_all_shows_everything(self, client, administrator):
        DataQualityFinding.objects.create(
            dedup_key="a",
            issue_type="missing_location",
            severity="high",
            status=DataQualityStatus.RESOLVED,
            object_type="UnitAsset",
            object_id="1",
            explanation="resolved one",
        )
        client.force_login(administrator)
        response = client.get(reverse("dataquality:workspace"), {"status": "all"})
        assert len(response.context["findings"]) == 1

    def test_run_detection_view_requires_administrator(
        self, client, stock_manager_with_room_access
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.post(reverse("dataquality:run_detection"))
        assert response.status_code == 403

    def test_run_detection_view_runs_a_scan(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-VIEW-SCAN",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-VIEW-SCAN")
        UnitAsset.objects.filter(pk=asset.pk).update(current_location=None)
        client.force_login(administrator)
        response = client.post(reverse("dataquality:run_detection"), follow=True)
        assert response.status_code == 200
        assert DataQualityFinding.objects.filter(object_id=str(asset.pk)).exists()

    def test_resolve_view(self, client, administrator):
        finding = DataQualityFinding.objects.create(
            dedup_key="c",
            issue_type="missing_location",
            severity="high",
            object_type="UnitAsset",
            object_id="5",
            explanation="test",
        )
        client.force_login(administrator)
        response = client.post(
            reverse("dataquality:resolve_finding", kwargs={"pk": finding.pk}),
            {"resolution_note": "done"},
            follow=True,
        )
        assert response.status_code == 200
        finding.refresh_from_db()
        assert finding.status == DataQualityStatus.RESOLVED

    def test_dismiss_view(self, client, administrator):
        finding = DataQualityFinding.objects.create(
            dedup_key="d",
            issue_type="missing_location",
            severity="high",
            object_type="UnitAsset",
            object_id="6",
            explanation="test",
        )
        client.force_login(administrator)
        response = client.post(
            reverse("dataquality:dismiss_finding", kwargs={"pk": finding.pk}), follow=True
        )
        assert response.status_code == 200
        finding.refresh_from_db()
        assert finding.status == DataQualityStatus.DISMISSED

    def test_csv_export(self, client, administrator):
        DataQualityFinding.objects.create(
            dedup_key="e",
            issue_type="missing_location",
            severity="high",
            object_type="UnitAsset",
            object_id="7",
            explanation="export me",
        )
        client.force_login(administrator)
        response = client.get(reverse("dataquality:workspace"), {"format": "csv"})
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert "export me" in response.content.decode()


@pytest.mark.django_db
class TestCorrectFindingView:
    def test_get_renders_form(self, client, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CORRECTVIEW",
            stock_purpose=StockPurpose.CUSTOMER,
            final_customer="Acme",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CORRECTVIEW")
        UnitAsset.objects.filter(pk=asset.pk).update(final_customer="", project_reference="")
        run_detection(user=None)
        finding = DataQualityFinding.objects.get(
            issue_type="customer_stock_missing_reference", object_id=str(asset.pk)
        )
        client.force_login(administrator)
        response = client.get(reverse("dataquality:correct_finding", kwargs={"pk": finding.pk}))
        assert response.status_code == 200

    def test_post_applies_correction_and_resolves_finding(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-CORRECTVIEW-POST",
            stock_purpose=StockPurpose.CUSTOMER,
            final_customer="Acme",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-CORRECTVIEW-POST")
        UnitAsset.objects.filter(pk=asset.pk).update(final_customer="", project_reference="")
        run_detection(user=None)
        finding = DataQualityFinding.objects.get(
            issue_type="customer_stock_missing_reference", object_id=str(asset.pk)
        )
        client.force_login(administrator)
        response = client.post(
            reverse("dataquality:correct_finding", kwargs={"pk": finding.pk}),
            {"final_customer": "Acme Corp", "project_reference": "", "reason": "fixed"},
            follow=True,
        )
        assert response.status_code == 200
        asset.refresh_from_db()
        assert asset.final_customer == "Acme Corp"
        finding.refresh_from_db()
        assert finding.status == DataQualityStatus.RESOLVED


@pytest.mark.django_db
class TestLinkedFindingResolutionFromAdminCorrectionViews:
    def test_correcting_a_unit_via_the_finding_link_resolves_it(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-LINKED-CORRECTION",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-LINKED-CORRECTION")
        UnitAsset.objects.filter(pk=asset.pk).update(current_location=None)
        run_detection(user=None)
        finding = DataQualityFinding.objects.get(
            issue_type="missing_location", object_id=str(asset.pk)
        )
        client.force_login(administrator)
        response = client.post(
            f"{reverse('inventory:asset_correct', kwargs={'pk': asset.pk})}",
            {
                "to_status": UnitStatus.IN_STOCK,
                "to_location": location_tree["room"].pk,
                "occurred_at": date.today().isoformat(),
                "reason": "fixed from data quality centre",
                "finding": str(finding.pk),
            },
            follow=True,
        )
        assert response.status_code == 200
        finding.refresh_from_db()
        assert finding.status == DataQualityStatus.RESOLVED

    def test_visiting_the_correction_page_without_a_finding_param_is_unaffected(
        self, client, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-NOLINK-CORRECTION",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-NOLINK-CORRECTION")
        client.force_login(administrator)
        response = client.post(
            reverse("inventory:asset_correct", kwargs={"pk": asset.pk}),
            {
                "to_status": UnitStatus.DAMAGED,
                "occurred_at": date.today().isoformat(),
                "reason": "unrelated correction",
            },
            follow=True,
        )
        assert response.status_code == 200
