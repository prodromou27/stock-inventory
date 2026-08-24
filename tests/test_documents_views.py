from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import Attachment, GeneratedDocument
from apps.documents.services import generate_document, upload_attachment
from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import assign_to_employee
from apps.inventory.services.receipts import receive_stock


@pytest.fixture
def other_room(administrator, other_location_tree):
    from apps.locations.models import Location
    from apps.locations.services import create_location

    other_floor = create_location(
        level=Location.Level.FLOOR,
        name="Doc Floor",
        parent=other_location_tree["site"],
        user=administrator,
    )
    return create_location(
        level=Location.Level.STORAGE_ROOM, name="Doc Room", parent=other_floor, user=administrator
    )


@pytest.fixture
def assignment_txn(stock_manager_with_room_access, unit_product, location_tree):
    receive_stock(
        user=stock_manager_with_room_access,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="SN-DOCVIEW-1",
    )
    asset = UnitAsset.objects.get(vendor_serial="SN-DOCVIEW-1")
    return assign_to_employee(
        user=stock_manager_with_room_access,
        employee_name="Oscar",
        occurred_at=date.today(),
        unit_asset_ids=[asset.pk],
    )


@pytest.mark.django_db
class TestGenerateDocumentView:
    def test_stock_manager_can_generate(
        self, client, stock_manager_with_room_access, assignment_txn
    ):
        client.force_login(stock_manager_with_room_access)
        response = client.post(
            reverse("documents:generate_document", kwargs={"pk": assignment_txn.pk})
        )
        assert response.status_code == 302
        assert GeneratedDocument.objects.filter(transaction=assignment_txn).exists()

    def test_read_only_user_forbidden(self, client, read_only_user, assignment_txn):
        client.force_login(read_only_user)
        response = client.post(
            reverse("documents:generate_document", kwargs={"pk": assignment_txn.pk})
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestDocumentDownloadView:
    def test_download_returns_pdf_bytes(
        self, client, stock_manager_with_room_access, assignment_txn
    ):
        document = generate_document(txn=assignment_txn, user=stock_manager_with_room_access)

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("documents:document_download", kwargs={"pk": document.pk}))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        content = b"".join(response.streaming_content)
        assert content[:4] == b"%PDF"

    def test_download_denied_outside_scope(
        self, client, administrator, stock_manager_with_room_access, unit_product, other_room
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-DOCVIEW-2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DOCVIEW-2")
        other_txn = assign_to_employee(
            user=administrator,
            employee_name="Peggy",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        document = generate_document(txn=other_txn, user=administrator)

        client.force_login(stock_manager_with_room_access)
        response = client.get(reverse("documents:document_download", kwargs={"pk": document.pk}))
        assert response.status_code == 403

    def test_anonymous_redirected(self, client, administrator, assignment_txn):
        document = generate_document(txn=assignment_txn, user=administrator)
        response = client.get(reverse("documents:document_download", kwargs={"pk": document.pk}))
        assert response.status_code == 302


@pytest.mark.django_db
class TestAttachmentUploadView:
    def test_stock_manager_can_upload(self, client, stock_manager_with_room_access, assignment_txn):
        client.force_login(stock_manager_with_room_access)
        upload = SimpleUploadedFile("signed.pdf", b"%PDF-1.4 x", content_type="application/pdf")
        response = client.post(
            reverse("documents:attachment_upload", kwargs={"pk": assignment_txn.pk}),
            {"file": upload},
        )
        assert response.status_code == 302
        assert Attachment.objects.filter(transaction=assignment_txn).exists()

    def test_invalid_file_shows_form_error(
        self, client, stock_manager_with_room_access, assignment_txn
    ):
        client.force_login(stock_manager_with_room_access)
        upload = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        response = client.post(
            reverse("documents:attachment_upload", kwargs={"pk": assignment_txn.pk}),
            {"file": upload},
        )
        assert response.status_code == 200
        assert response.context["form"].errors
        assert not Attachment.objects.filter(transaction=assignment_txn).exists()

    def test_read_only_user_forbidden(self, client, read_only_user, assignment_txn):
        client.force_login(read_only_user)
        response = client.get(
            reverse("documents:attachment_upload", kwargs={"pk": assignment_txn.pk})
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestAttachmentDownloadAndDelete:
    def test_download_denied_outside_scope(
        self, client, administrator, stock_manager_with_room_access, unit_product, other_room
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=other_room,
            occurred_at=date.today(),
            vendor_serial="SN-DOCVIEW-3",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-DOCVIEW-3")
        other_txn = assign_to_employee(
            user=administrator,
            employee_name="Quinn",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )
        attachment = upload_attachment(
            txn=other_txn,
            uploaded_file=SimpleUploadedFile(
                "x.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
            user=administrator,
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(
            reverse("documents:attachment_download", kwargs={"pk": attachment.pk})
        )
        assert response.status_code == 403

    def test_download_within_scope_succeeds(
        self, client, stock_manager_with_room_access, assignment_txn
    ):
        attachment = upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "x.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
            user=stock_manager_with_room_access,
        )

        client.force_login(stock_manager_with_room_access)
        response = client.get(
            reverse("documents:attachment_download", kwargs={"pk": attachment.pk})
        )
        assert response.status_code == 200

    def test_deleted_attachment_returns_404(
        self, client, administrator, stock_manager_with_room_access, assignment_txn
    ):
        attachment = upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "x.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
            user=stock_manager_with_room_access,
        )
        client.force_login(administrator)
        client.post(reverse("documents:attachment_delete", kwargs={"pk": attachment.pk}))

        response = client.get(
            reverse("documents:attachment_download", kwargs={"pk": attachment.pk})
        )
        assert response.status_code == 404

    def test_stock_manager_cannot_delete(
        self, client, stock_manager_with_room_access, assignment_txn
    ):
        attachment = upload_attachment(
            txn=assignment_txn,
            uploaded_file=SimpleUploadedFile(
                "x.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            ),
            user=stock_manager_with_room_access,
        )
        client.force_login(stock_manager_with_room_access)
        response = client.post(reverse("documents:attachment_delete", kwargs={"pk": attachment.pk}))
        assert response.status_code == 403
        attachment.refresh_from_db()
        assert attachment.is_deleted is False
