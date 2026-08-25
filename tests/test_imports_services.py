import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.audit.models import AuditEvent
from apps.catalog.models import Product, TrackingMethod
from apps.imports import services
from apps.imports.models import ImportBatchStatus, ImportRowOutcome
from apps.inventory.models import InventoryTransaction, UnitAsset

from .imports_fixture_builder import build_legacy_workbook_bytes

COLUMNS = services.parsing.COLUMNS


def _csv_upload(rows, filename="test.csv"):
    lines = [",".join(COLUMNS)]
    for row in rows:
        values = [str(row.get(col, "")) for col in COLUMNS]
        lines.append(",".join(values))
    content = ("\n".join(lines) + "\n").encode("utf-8")
    return SimpleUploadedFile(filename, content, content_type="text/csv")


def _base_row(**overrides):
    row = {
        "BRAND": "Fortinet",
        "MODEL/Part No./SKU": "FG-100F",
        "TYPE/DESCRIPTION": "Firewall",
        "S/N": "SNIMP001",
        "QTY": "1",
        "LOCATION": "Room A",
    }
    row.update(overrides)
    return row


@pytest.mark.django_db
class TestStageRow:
    def test_missing_brand_model_type_is_failed(self):
        normalized, outcome, detail = services._stage_row(
            {"BRAND": "", "MODEL/Part No./SKU": "", "TYPE/DESCRIPTION": "", "S/N": "", "QTY": ""}
        )
        assert outcome == ImportRowOutcome.FAILED
        assert "Brand is required" in detail
        assert "Model is required" in detail
        assert "Type is required" in detail

    def test_invalid_quantity_is_failed(self, location_tree):
        raw = _base_row(LOCATION="Room A", **{"S/N": "", "QTY": "not-a-number"})
        normalized, outcome, detail = services._stage_row(raw)
        assert outcome == ImportRowOutcome.FAILED
        assert "not a valid" in detail

    def test_blank_serial_needs_positive_quantity(self, location_tree):
        raw = _base_row(LOCATION="Room A", **{"S/N": "", "QTY": "0"})
        normalized, outcome, detail = services._stage_row(raw)
        assert outcome == ImportRowOutcome.FAILED
        assert "positive QTY" in detail

    def test_unresolved_location_is_warning(self):
        raw = _base_row(LOCATION="Nonexistent Place")
        normalized, outcome, detail = services._stage_row(raw)
        assert outcome == ImportRowOutcome.WARNING
        assert "Unknown location" in detail
        assert normalized["resolved_location_id"] is None

    def test_clean_row_is_pending(self, location_tree):
        raw = _base_row(LOCATION="Room A")
        normalized, outcome, detail = services._stage_row(raw)
        assert outcome == ImportRowOutcome.PENDING
        assert detail == ""
        assert normalized["resolved_location_id"] == str(location_tree["room"].pk)
        assert normalized["tracking_method"] == TrackingMethod.UNIT

    def test_quantity_row_without_serial_is_pending_when_valid(self, location_tree):
        raw = _base_row(LOCATION="Room A", **{"S/N": "", "QTY": "5"})
        normalized, outcome, detail = services._stage_row(raw)
        assert outcome == ImportRowOutcome.PENDING
        assert normalized["tracking_method"] == TrackingMethod.QUANTITY
        assert normalized["quantity"] == 5

    def test_duplicate_serial_is_warning(self, administrator, location_tree, unit_product):
        from apps.inventory.services.receipts import receive_stock

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at="2024-01-01",
            vendor_serial="SNIMP001",
        )
        raw = _base_row(LOCATION="Room A")
        normalized, outcome, detail = services._stage_row(raw)
        assert outcome == ImportRowOutcome.WARNING
        assert "matches 1 existing unit asset" in detail

    def test_duplicate_product_is_warning(self, administrator, location_tree, unit_product):
        raw = _base_row(
            LOCATION="Room A",
            BRAND=unit_product.brand.name,
            **{"MODEL/Part No./SKU": unit_product.model, "S/N": "SN-OTHER"},
        )
        normalized, outcome, detail = services._stage_row(raw)
        assert outcome == ImportRowOutcome.WARNING
        assert "matches 1 existing product" in detail

    def test_tracking_method_conflict_is_failed(
        self, administrator, location_tree, quantity_product
    ):
        raw = _base_row(
            LOCATION="Room A",
            BRAND=quantity_product.brand.name,
            **{"MODEL/Part No./SKU": quantity_product.model, "S/N": "SN-CONFLICT"},
        )
        normalized, outcome, detail = services._stage_row(raw)
        assert outcome == ImportRowOutcome.FAILED
        assert "already exists as Quantity-tracked" in detail

    def test_legacy_columns_preserved_in_notes(self):
        raw = _base_row(
            **{
                "COMMENTS/#No": "Legacy ref 123",
                "PRODUCT DELIVERY / PRODUCT REMOVAL": "Delivered",
                "Registrar": "J. Smith",
            }
        )
        normalized, outcome, detail = services._stage_row(raw)
        assert "Legacy ref 123" in normalized["notes"]
        assert "Delivered" in normalized["notes"]
        assert "J. Smith" in normalized["notes"]


@pytest.mark.django_db
class TestCreateBatchFromUpload:
    def test_stages_all_rows_and_records_audit_event(self, administrator, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Room A", **{"S/N": f"SN-{i}"}) for i in range(3)])
        batch, is_repeat = services.create_batch_from_upload(
            uploaded_file=upload, user=administrator
        )
        assert batch.row_count() == 3
        assert batch.status == ImportBatchStatus.PREVIEWED
        assert is_repeat is False
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.RECORD_CREATED, object_id=str(batch.pk)
        ).exists()

    def test_requires_administrator(self, stock_manager, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Room A")])
        with pytest.raises(PermissionDenied):
            services.create_batch_from_upload(uploaded_file=upload, user=stock_manager)

    def test_legacy_workbook_layout_stages_without_crashing(self, administrator, location_tree):
        upload = SimpleUploadedFile(
            "legacy_sample.xlsx",
            build_legacy_workbook_bytes(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        assert batch.row_count() == 7
        # LOCATION="Customer" rows can't resolve against location_tree's "Room A", so they warn.
        assert batch.rows.filter(outcome=ImportRowOutcome.WARNING).count() >= 2


@pytest.mark.django_db
class TestExecuteBatch:
    def test_executes_pending_rows_and_dedupes_products(self, administrator, location_tree):
        upload = _csv_upload(
            [
                _base_row(LOCATION="Room A", **{"S/N": "SN-A"}),
                _base_row(LOCATION="Room A", **{"S/N": "SN-B"}),
            ]
        )
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        services.execute_batch(batch=batch, user=administrator)
        batch.refresh_from_db()

        assert batch.status == ImportBatchStatus.COMPLETED
        assert batch.imported_count == 2
        assert UnitAsset.objects.filter(vendor_serial__in=["SN-A", "SN-B"]).count() == 2
        assert (
            Product.objects.filter(brand__name="Fortinet", normalized_model="fg-100f").count() == 1
        )

    def test_idempotent_on_retry(self, administrator, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Room A", **{"S/N": "SN-IDEMPOTENT"})])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        services.execute_batch(batch=batch, user=administrator)
        txn_count_after_first = InventoryTransaction.objects.count()

        services.execute_batch(batch=batch, user=administrator)
        batch.refresh_from_db()

        assert InventoryTransaction.objects.count() == txn_count_after_first
        assert batch.imported_count == 1
        assert UnitAsset.objects.filter(vendor_serial="SN-IDEMPOTENT").count() == 1

    def test_second_executor_is_rejected_while_batch_is_running(self, administrator, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Room A", **{"S/N": "SN-CONCURRENT"})])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        batch.status = ImportBatchStatus.EXECUTING
        batch.save(update_fields=["status"])

        with pytest.raises(ValidationError, match="already executing"):
            services.execute_batch(batch=batch, user=administrator)
        assert not UnitAsset.objects.filter(vendor_serial="SN-CONCURRENT").exists()

    def test_duplicate_import_requires_explicit_row_acknowledgement(
        self, administrator, location_tree, unit_product
    ):
        from apps.inventory.services.receipts import receive_stock

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at="2024-01-01",
            vendor_serial="SN-IMPORT-ACK",
        )
        upload = _csv_upload(
            [
                _base_row(
                    LOCATION="Room A",
                    BRAND=unit_product.brand.name,
                    **{
                        "MODEL/Part No./SKU": unit_product.model,
                        "S/N": "SN-IMPORT-ACK",
                    },
                )
            ]
        )
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        services.execute_batch(batch=batch, user=administrator)
        assert UnitAsset.objects.filter(vendor_serial="SN-IMPORT-ACK").count() == 1

        batch.refresh_from_db()
        row = batch.rows.get()
        services.acknowledge_row_duplicate_serial(row=row, user=administrator)
        services.execute_batch(batch=batch, user=administrator)

        assert UnitAsset.objects.filter(vendor_serial="SN-IMPORT-ACK").count() == 2
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.DUPLICATE_SERIAL_ACKNOWLEDGED,
            object_id=str(row.pk),
        ).exists()

    def test_row_with_unresolved_location_is_not_executed(self, administrator, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Nowhere Real", **{"S/N": "SN-NOLOC"})])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        services.execute_batch(batch=batch, user=administrator)
        batch.refresh_from_db()

        assert batch.status == ImportBatchStatus.PARTIALLY_COMPLETED
        assert batch.warning_count == 1
        assert not UnitAsset.objects.filter(vendor_serial="SN-NOLOC").exists()

    def test_override_then_execute_succeeds(self, administrator, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Nowhere Real", **{"S/N": "SN-OVERRIDE"})])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        row = batch.rows.get()

        services.set_row_location_override(
            row=row, location=location_tree["room"], user=administrator
        )
        services.execute_batch(batch=batch, user=administrator)
        batch.refresh_from_db()

        assert batch.status == ImportBatchStatus.COMPLETED
        asset = UnitAsset.objects.get(vendor_serial="SN-OVERRIDE")
        assert asset.current_location == location_tree["room"]

    def test_preview_override_and_skip_are_audited(self, administrator, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Nowhere", **{"S/N": "SN-AUDIT-PREVIEW"})])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        row = batch.rows.get()
        services.set_row_location_override(
            row=row, location=location_tree["room"], user=administrator
        )
        services.skip_row(row=row, user=administrator)

        events = AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.RECORD_UPDATED, object_id=str(row.pk)
        ).order_by("occurred_at")
        assert events.count() == 2
        assert events[0].new_values["location_override_id"] == str(location_tree["room"].pk)
        assert events[1].old_values["outcome"] == ImportRowOutcome.WARNING
        assert events[1].new_values["outcome"] == ImportRowOutcome.SKIPPED

    def test_override_allowed_after_partial_execution(self, administrator, location_tree):
        """Regression: after a first execute() leaves a batch partially_completed,
        remaining warning rows must still be editable — that's the entire point
        of the retry workflow (spec §13's idempotent-retry requirement).
        """
        upload = _csv_upload([_base_row(LOCATION="Nowhere Real", **{"S/N": "SN-RETRY"})])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        services.execute_batch(batch=batch, user=administrator)
        batch.refresh_from_db()
        assert batch.status == ImportBatchStatus.PARTIALLY_COMPLETED

        row = batch.rows.get()
        services.set_row_location_override(
            row=row, location=location_tree["room"], user=administrator
        )
        services.execute_batch(batch=batch, user=administrator)
        batch.refresh_from_db()

        assert batch.status == ImportBatchStatus.COMPLETED
        assert UnitAsset.objects.filter(vendor_serial="SN-RETRY").exists()

    def test_skip_row_excludes_from_execution(self, administrator, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Room A", **{"S/N": "SN-SKIP"})])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        row = batch.rows.get()

        services.skip_row(row=row, user=administrator)
        services.execute_batch(batch=batch, user=administrator)
        batch.refresh_from_db()

        assert batch.skipped_count == 1
        assert batch.imported_count == 0
        assert not UnitAsset.objects.filter(vendor_serial="SN-SKIP").exists()

    def test_cannot_edit_rows_of_a_fully_completed_batch(self, administrator, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Room A", **{"S/N": "SN-DONE"})])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        services.execute_batch(batch=batch, user=administrator)
        batch.refresh_from_db()
        assert batch.status == ImportBatchStatus.COMPLETED

        row = batch.rows.get()
        with pytest.raises(ValidationError):
            services.skip_row(row=row, user=administrator)

    def test_requires_administrator(self, stock_manager, location_tree):
        with pytest.raises(PermissionDenied):
            services.execute_batch(batch=None, user=stock_manager)


@pytest.mark.django_db
class TestDownloads:
    def test_template_csv_round_trips_through_the_parser(self, administrator, location_tree):
        content = services.build_template_csv()
        upload = SimpleUploadedFile(
            "template.csv", content.encode("utf-8"), content_type="text/csv"
        )
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        assert batch.row_count() == 2
        assert batch.rows.filter(outcome=ImportRowOutcome.FAILED).count() == 0

    def test_results_csv_lists_every_row(self, administrator, location_tree):
        upload = _csv_upload([_base_row(LOCATION="Room A", **{"S/N": "SN-RESULT"})])
        batch, _ = services.create_batch_from_upload(uploaded_file=upload, user=administrator)
        services.execute_batch(batch=batch, user=administrator)
        content = services.build_results_csv(batch)
        assert "SN-RESULT" in content
        assert "Imported" in content
