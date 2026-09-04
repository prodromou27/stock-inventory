from datetime import date
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.audit.models import AuditEvent
from apps.catalog.services import update_product
from apps.documents.models import Attachment, GeneratedDocument
from apps.documents.services import (
    delete_attachment,
    generate_document,
    regenerate_document,
    upload_attachment,
)
from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import assign_to_employee, deliver_to_customer
from apps.inventory.services.disposition import dispose
from apps.inventory.services.receipts import receive_stock


@pytest.fixture
def disposal_txn(administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="SN-DOC-DISPOSAL",
    )
    asset = UnitAsset.objects.get(vendor_serial="SN-DOC-DISPOSAL")
    return dispose(
        user=administrator,
        occurred_at=date.today(),
        unit_asset_ids=[asset.pk],
        notes="end of life",
        wipe_method="software_wipe",
        witness_name="R. Patel",
    )


@pytest.fixture
def assignment_txn(administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="SN-DOC-1",
    )
    asset = UnitAsset.objects.get(vendor_serial="SN-DOC-1")
    return assign_to_employee(
        user=administrator,
        employee_name="Nadia",
        occurred_at=date.today(),
        unit_asset_ids=[asset.pk],
        project_reference="PRJ-DOC",
        condition="good",
        accessories="charger",
    )


@pytest.mark.django_db
class TestGenerateDocument:
    def test_database_failure_removes_generated_pdf(
        self, administrator, assignment_txn, monkeypatch
    ):
        before = set(Path(settings.MEDIA_ROOT).rglob("*"))

        def fail_audit(**kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr("apps.documents.services.record_event", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            generate_document(txn=assignment_txn, user=administrator)

        after = set(Path(settings.MEDIA_ROOT).rglob("*"))
        assert {path for path in after - before if path.is_file()} == set()
        assert not GeneratedDocument.objects.filter(transaction=assignment_txn).exists()

    def test_rejects_a_movement_type_with_no_printable_document(
        self, administrator, unit_product, location_tree
    ):
        txn = receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DOC-RECEIPT",
        )
        with pytest.raises(ValidationError):
            generate_document(txn=txn, user=administrator)

    def test_disposal_generates_a_disposal_certificate(self, administrator, disposal_txn):
        document = generate_document(txn=disposal_txn, user=administrator)
        assert document.document_type == "disposal"
        assert document.context_snapshot["wipe_method_display"] == "Software data wipe"
        assert document.context_snapshot["witness_name"] == "R. Patel"

    def test_non_disposal_context_has_blank_wipe_fields(self, administrator, assignment_txn):
        document = generate_document(txn=assignment_txn, user=administrator)
        assert document.context_snapshot["wipe_method_display"] == ""
        assert document.context_snapshot["witness_name"] == ""

    def test_generates_a_real_pdf(self, administrator, assignment_txn):
        document = generate_document(txn=assignment_txn, user=administrator)

        assert document.pdf_file.name
        content = document.pdf_file.open("rb").read()
        assert content[:4] == b"%PDF"
        assert len(content) > 100

    def test_document_number_is_sequential(self, administrator, unit_product, location_tree):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DOC-SEQ1",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DOC-SEQ2",
        )
        asset1 = UnitAsset.objects.get(vendor_serial="SN-DOC-SEQ1")
        asset2 = UnitAsset.objects.get(vendor_serial="SN-DOC-SEQ2")
        txn1 = deliver_to_customer(
            user=administrator,
            final_customer="A",
            occurred_at=date.today(),
            unit_asset_ids=[asset1.pk],
        )
        txn2 = deliver_to_customer(
            user=administrator,
            final_customer="B",
            occurred_at=date.today(),
            unit_asset_ids=[asset2.pk],
        )

        doc1 = generate_document(txn=txn1, user=administrator)
        doc2 = generate_document(txn=txn2, user=administrator)

        assert doc1.document_number.startswith("DOC-")
        assert doc2.document_number.startswith("DOC-")
        assert doc1.document_number != doc2.document_number
        num1 = int(doc1.document_number.split("-")[1])
        num2 = int(doc2.document_number.split("-")[1])
        assert num2 > num1

    def test_context_snapshot_unaffected_by_later_product_edit(
        self, administrator, assignment_txn, unit_product
    ):
        document = generate_document(txn=assignment_txn, user=administrator)
        original_model = document.context_snapshot["lines"][0]["model"]

        update_product(
            product=unit_product,
            user=administrator,
            brand_name=unit_product.brand.name,
            model="RENAMED-AFTER-DOC",
            product_type_name=unit_product.product_type.name,
            tracking_method=unit_product.tracking_method,
        )

        document.refresh_from_db()
        assert document.context_snapshot["lines"][0]["model"] == original_model
        assert document.context_snapshot["lines"][0]["model"] != "RENAMED-AFTER-DOC"

    def test_pdf_file_unaffected_by_later_product_edit(
        self, administrator, assignment_txn, unit_product
    ):
        document = generate_document(txn=assignment_txn, user=administrator)
        original_pdf_bytes = document.pdf_file.open("rb").read()

        update_product(
            product=unit_product,
            user=administrator,
            brand_name=unit_product.brand.name,
            model="RENAMED-AGAIN",
            product_type_name=unit_product.product_type.name,
            tracking_method=unit_product.tracking_method,
        )

        document.refresh_from_db()
        assert document.pdf_file.open("rb").read() == original_pdf_bytes

    def test_cannot_generate_for_receipt_transaction(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-DOC-RECEIPT",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DOC-RECEIPT")
        receipt_txn = asset.transaction_lines.first().transaction

        with pytest.raises(ValidationError):
            generate_document(txn=receipt_txn, user=administrator)

    def test_generation_is_audited(self, administrator, assignment_txn):
        document = generate_document(txn=assignment_txn, user=administrator)

        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.DOCUMENT_GENERATED, object_id=str(document.pk)
        ).exists()

    def test_scope_enforced(self, stock_manager, assignment_txn):
        with pytest.raises(PermissionDenied):
            generate_document(txn=assignment_txn, user=stock_manager)

    def test_read_only_user_cannot_generate(self, read_only_user, assignment_txn):
        with pytest.raises(Exception):
            generate_document(txn=assignment_txn, user=read_only_user)

    def test_document_cannot_be_updated_after_creation(self, administrator, assignment_txn):
        document = generate_document(txn=assignment_txn, user=administrator)
        document.template_version = "tampered"
        with pytest.raises(ValueError):
            document.save()


@pytest.mark.django_db
class TestRegenerateDocument:
    def test_creates_new_row_linked_via_supersedes(self, administrator, assignment_txn):
        original = generate_document(txn=assignment_txn, user=administrator)
        regenerated = regenerate_document(previous_document=original, user=administrator)

        assert regenerated.pk != original.pk
        assert regenerated.document_number != original.document_number
        assert regenerated.supersedes == original
        assert GeneratedDocument.objects.filter(pk=original.pk).exists()

    def test_original_pdf_file_untouched(self, administrator, assignment_txn):
        original = generate_document(txn=assignment_txn, user=administrator)
        original_bytes = original.pdf_file.open("rb").read()

        regenerate_document(previous_document=original, user=administrator)

        original.refresh_from_db()
        assert original.pdf_file.open("rb").read() == original_bytes


@pytest.mark.django_db
class TestUploadAttachment:
    def test_database_failure_removes_uploaded_file(
        self, administrator, assignment_txn, monkeypatch
    ):
        before = set(Path(settings.MEDIA_ROOT).rglob("*"))
        upload = SimpleUploadedFile(
            "signed.pdf", b"%PDF-1.4 signed", content_type="application/pdf"
        )

        def fail_audit(**kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr("apps.documents.services.record_event", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            upload_attachment(txn=assignment_txn, uploaded_file=upload, user=administrator)

        after = set(Path(settings.MEDIA_ROOT).rglob("*"))
        assert {path for path in after - before if path.is_file()} == set()
        assert not Attachment.objects.filter(transaction=assignment_txn).exists()

    def test_uploads_valid_pdf(self, administrator, assignment_txn):
        upload = SimpleUploadedFile("form.pdf", b"%PDF-1.4 content", content_type="application/pdf")
        attachment = upload_attachment(txn=assignment_txn, uploaded_file=upload, user=administrator)

        assert attachment.content_type == "application/pdf"
        assert attachment.original_filename == "form.pdf"

    def test_uploads_valid_png(self, administrator, assignment_txn):
        upload = SimpleUploadedFile(
            "photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, content_type="image/png"
        )
        attachment = upload_attachment(txn=assignment_txn, uploaded_file=upload, user=administrator)
        assert attachment.content_type == "image/png"

    def test_rejects_disallowed_extension(self, administrator, assignment_txn):
        upload = SimpleUploadedFile("virus.exe", b"MZ\x90\x00", content_type="application/pdf")
        with pytest.raises(ValidationError):
            upload_attachment(txn=assignment_txn, uploaded_file=upload, user=administrator)

    def test_rejects_content_not_matching_allowed_signature(self, administrator, assignment_txn):
        upload = SimpleUploadedFile("form.pdf", b"not a real pdf", content_type="application/pdf")
        with pytest.raises(ValidationError):
            upload_attachment(txn=assignment_txn, uploaded_file=upload, user=administrator)

    def test_rejects_oversized_file(self, administrator, assignment_txn, monkeypatch):
        import apps.documents.services as services_module

        monkeypatch.setattr(services_module, "MAX_ATTACHMENT_SIZE_BYTES", 10)
        upload = SimpleUploadedFile(
            "form.pdf", b"%PDF-1.4 more than ten bytes", content_type="application/pdf"
        )
        with pytest.raises(ValidationError):
            upload_attachment(txn=assignment_txn, uploaded_file=upload, user=administrator)

    def test_storage_filename_is_not_derived_from_client_filename(
        self, administrator, assignment_txn
    ):
        upload = SimpleUploadedFile(
            "../../etc/passwd.pdf", b"%PDF-1.4 x", content_type="application/pdf"
        )
        attachment = upload_attachment(txn=assignment_txn, uploaded_file=upload, user=administrator)

        assert "../" not in attachment.file.name
        assert str(attachment.id) in attachment.file.name

    def test_second_upload_creates_new_row_never_overwrites(self, administrator, assignment_txn):
        first = upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "form.pdf", b"%PDF-1.4 one", content_type="application/pdf"
            ),
            user=administrator,
        )
        second = upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "form.pdf", b"%PDF-1.4 two", content_type="application/pdf"
            ),
            user=administrator,
        )

        assert first.pk != second.pk
        assert Attachment.objects.filter(transaction=assignment_txn).count() == 2
        assert first.file.open("rb").read() != second.file.open("rb").read()

    def test_upload_is_audited(self, administrator, assignment_txn):
        upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "form.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
            user=administrator,
        )
        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.ATTACHMENT_UPLOADED
        ).exists()

    def test_scope_enforced(self, stock_manager, assignment_txn):
        upload = SimpleUploadedFile("form.pdf", b"%PDF-1.4 x", content_type="application/pdf")
        with pytest.raises(PermissionDenied):
            upload_attachment(txn=assignment_txn, uploaded_file=upload, user=stock_manager)


@pytest.mark.django_db
class TestDeleteAttachment:
    def test_administrator_can_soft_delete(self, administrator, assignment_txn):
        attachment = upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "form.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
            user=administrator,
        )
        delete_attachment(attachment=attachment, user=administrator)

        attachment.refresh_from_db()
        assert attachment.is_deleted is True
        # file is retained on disk, not purged
        assert attachment.file.storage.exists(attachment.file.name)

    def test_stock_manager_cannot_delete(self, administrator, stock_manager, assignment_txn):
        attachment = upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "form.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
            user=administrator,
        )
        with pytest.raises(Exception):
            delete_attachment(attachment=attachment, user=stock_manager)

    def test_cannot_delete_twice(self, administrator, assignment_txn):
        attachment = upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "form.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
            user=administrator,
        )
        delete_attachment(attachment=attachment, user=administrator)
        with pytest.raises(ValidationError):
            delete_attachment(attachment=attachment, user=administrator)

    def test_deletion_is_audited(self, administrator, assignment_txn):
        attachment = upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "form.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
            user=administrator,
        )
        delete_attachment(attachment=attachment, user=administrator)

        assert AuditEvent.objects.filter(
            event_type=AuditEvent.EventType.ATTACHMENT_DELETED
        ).exists()
