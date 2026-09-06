from datetime import date

import pytest
from django.core.management import call_command

from apps.documents.integrity import check_document_integrity
from apps.documents.models import DocumentIntegrityCheckRun
from apps.documents.services import generate_document
from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import assign_to_employee
from apps.inventory.services.receipts import receive_stock


@pytest.fixture
def assignment_txn(administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="SN-INTEGRITY-1",
    )
    asset = UnitAsset.objects.get(vendor_serial="SN-INTEGRITY-1")
    return assign_to_employee(
        user=administrator,
        employee_name="Priya",
        occurred_at=date.today(),
        unit_asset_ids=[asset.pk],
    )


@pytest.mark.django_db
class TestCheckDocumentIntegrity:
    def test_no_documents_is_clean(self):
        run = check_document_integrity()
        assert run.checked_count == 0
        assert run.missing_count == 0

    def test_healthy_document_is_not_flagged(self, administrator, assignment_txn):
        generate_document(txn=assignment_txn, user=administrator)
        run = check_document_integrity()
        assert run.checked_count == 1
        assert run.missing_count == 0

    def test_deleted_file_is_flagged(self, administrator, assignment_txn):
        document = generate_document(txn=assignment_txn, user=administrator)
        document.pdf_file.storage.delete(document.pdf_file.name)

        run = check_document_integrity()
        assert run.checked_count == 1
        assert run.missing_document_ids == [str(document.pk)]

    def test_corrupt_zero_byte_file_is_flagged(self, administrator, assignment_txn):
        document = generate_document(txn=assignment_txn, user=administrator)
        with document.pdf_file.storage.open(document.pdf_file.name, "wb") as f:
            f.write(b"")

        run = check_document_integrity()
        assert run.missing_document_ids == [str(document.pk)]

    def test_each_invocation_creates_a_new_run_row(self, administrator, assignment_txn):
        generate_document(txn=assignment_txn, user=administrator)
        check_document_integrity()
        check_document_integrity()
        assert DocumentIntegrityCheckRun.objects.count() == 2


@pytest.mark.django_db
class TestCheckDocumentIntegrityCommand:
    def test_reports_clean_result(self, administrator, assignment_txn, capsys):
        generate_document(txn=assignment_txn, user=administrator)
        call_command("check_document_integrity")
        assert "all 1 documents OK" in capsys.readouterr().out

    def test_reports_missing_files(self, administrator, assignment_txn, capsys):
        document = generate_document(txn=assignment_txn, user=administrator)
        document.pdf_file.storage.delete(document.pdf_file.name)

        call_command("check_document_integrity")
        assert "1 of 1 documents have a missing or corrupt PDF" in capsys.readouterr().out
