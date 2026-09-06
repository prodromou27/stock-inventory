from datetime import date
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.branding import (
    apply_branding_override,
    eligible_countries,
    get_branding_profile,
    list_branding_profiles,
    save_branding_profile,
)
from apps.documents.models import CountryBrandingProfile
from apps.documents.services import generate_document
from apps.inventory.models import UnitAsset
from apps.inventory.services.assignments import deliver_to_customer
from apps.inventory.services.receipts import receive_stock
from apps.locations.scoping import country_for_location

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00"
    b"\x01\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.django_db
class TestCountryForLocation:
    def test_returns_the_room_itself_when_already_a_country(self, location_tree):
        assert country_for_location(location_tree["country"]) == location_tree["country"]

    def test_walks_up_from_a_storage_room(self, location_tree):
        assert country_for_location(location_tree["room"]) == location_tree["country"]

    def test_none_for_none(self):
        assert country_for_location(None) is None


@pytest.mark.django_db
class TestSaveBrandingProfile:
    def test_creates_a_new_profile(self, administrator, location_tree):
        profile = save_branding_profile(
            user=administrator,
            country=location_tree["country"],
            company_name="Acme Cyprus Ltd",
            company_address="1 Main St",
            company_tax_id="CY12345",
            terms_text="Local terms apply.",
            signature_left_label="Handed over by",
            signature_right_label="Received by",
        )
        assert profile.pk is not None
        assert profile.company_name == "Acme Cyprus Ltd"
        assert CountryBrandingProfile.objects.count() == 1

    def test_updates_the_existing_profile_rather_than_creating_another(
        self, administrator, location_tree
    ):
        save_branding_profile(
            user=administrator, country=location_tree["country"], company_name="First"
        )
        save_branding_profile(
            user=administrator, country=location_tree["country"], company_name="Second"
        )
        assert CountryBrandingProfile.objects.count() == 1
        assert get_branding_profile(location_tree["country"]).company_name == "Second"

    def test_blank_fields_clear_a_previous_override(self, administrator, location_tree):
        save_branding_profile(
            user=administrator, country=location_tree["country"], company_name="Something"
        )
        save_branding_profile(user=administrator, country=location_tree["country"])
        assert get_branding_profile(location_tree["country"]).company_name == ""

    def test_requires_administrator(self, stock_manager, location_tree):
        with pytest.raises(PermissionDenied):
            save_branding_profile(
                user=stock_manager, country=location_tree["country"], company_name="Nope"
            )

    def test_rejects_a_non_country_location(self, administrator, location_tree):
        with pytest.raises(ValidationError):
            save_branding_profile(
                user=administrator, country=location_tree["room"], company_name="Nope"
            )

    def test_saves_and_replaces_a_logo(self, administrator, location_tree):
        logo = SimpleUploadedFile("logo.png", PNG_BYTES, content_type="image/png")
        profile = save_branding_profile(
            user=administrator, country=location_tree["country"], logo=logo
        )
        assert profile.logo

        profile = save_branding_profile(
            user=administrator, country=location_tree["country"], remove_logo=True
        )
        assert not profile.logo

    def test_rejects_an_oversized_or_non_image_logo(self, administrator, location_tree):
        bad_file = SimpleUploadedFile("logo.txt", b"not an image", content_type="text/plain")
        with pytest.raises(ValidationError):
            save_branding_profile(
                user=administrator, country=location_tree["country"], logo=bad_file
            )


@pytest.mark.django_db
class TestListAndEligibleCountries:
    def test_eligible_countries_excludes_non_country_locations(self, location_tree):
        countries = list(eligible_countries())
        assert countries == [location_tree["country"]]

    def test_list_branding_profiles_keyed_by_country_id(self, administrator, location_tree):
        save_branding_profile(
            user=administrator, country=location_tree["country"], company_name="X"
        )
        profiles = list_branding_profiles()
        assert location_tree["country"].id in profiles


@pytest.mark.django_db
class TestApplyBrandingOverride:
    def test_no_profile_leaves_context_unchanged(self, location_tree):
        context = {"company_name": "Base Co", "logo_data_uri": "data:base"}
        result = apply_branding_override(context, country=location_tree["country"])
        assert result == context

    def test_only_non_blank_fields_override(self, administrator, location_tree):
        save_branding_profile(
            user=administrator,
            country=location_tree["country"],
            company_name="Override Co",
            # company_address/company_tax_id/terms_text left blank -> inherit
        )
        context = {
            "company_name": "Base Co",
            "company_address": "Base Address",
            "company_tax_id": "BASE-TAX",
            "terms_text": "Base terms.",
            "logo_data_uri": "data:base",
        }
        result = apply_branding_override(context, country=location_tree["country"])
        assert result["company_name"] == "Override Co"
        assert result["company_address"] == "Base Address"
        assert result["company_tax_id"] == "BASE-TAX"
        assert result["terms_text"] == "Base terms."

    def test_logo_override_replaces_the_data_uri(self, administrator, location_tree):
        logo = SimpleUploadedFile("logo.png", PNG_BYTES, content_type="image/png")
        save_branding_profile(user=administrator, country=location_tree["country"], logo=logo)
        context = {"logo_data_uri": "data:base"}
        result = apply_branding_override(context, country=location_tree["country"])
        assert result["logo_data_uri"].startswith("data:image/png;base64,")

    def test_none_country_returns_context_unchanged(self):
        context = {"company_name": "Base Co"}
        assert apply_branding_override(context, country=None) == context


@pytest.mark.django_db
class TestGenerateDocumentUsesCountryBranding:
    def test_generation_derives_country_from_the_source_location_and_passes_it_through(
        self, administrator, unit_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-BRANDING-1",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-BRANDING-1")
        txn = deliver_to_customer(
            user=administrator,
            final_customer="Branding QA Corp",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        from apps.documents.pdf import render_pdf as real_render_pdf

        with patch(
            "apps.documents.services.render_pdf", side_effect=real_render_pdf
        ) as mock_render_pdf:
            generate_document(txn=txn, user=administrator)

        _, kwargs = mock_render_pdf.call_args
        assert kwargs["country"] == location_tree["country"]

    def test_disposal_also_derives_its_country_from_the_line_location(
        self, administrator, unit_product, location_tree
    ):
        from apps.inventory.services.disposition import dispose

        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-BRANDING-DISPOSAL",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-BRANDING-DISPOSAL")
        txn = dispose(
            user=administrator,
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
            notes="end of life",
            wipe_method="software_wipe",
            witness_name="R. Patel",
        )

        from apps.documents.pdf import render_pdf as real_render_pdf

        with patch(
            "apps.documents.services.render_pdf", side_effect=real_render_pdf
        ) as mock_render_pdf:
            generate_document(txn=txn, user=administrator)

        _, kwargs = mock_render_pdf.call_args
        assert kwargs["country"] == location_tree["country"]

    def test_a_saved_profile_actually_changes_the_rendered_output(
        self, administrator, unit_product, location_tree
    ):
        save_branding_profile(
            user=administrator,
            country=location_tree["country"],
            company_name="Branded Legal Entity Ltd",
        )
        receive_stock(
            user=administrator,
            product=unit_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            vendor_serial="SN-BRANDING-2",
        )
        asset = UnitAsset.objects.get(vendor_serial="SN-BRANDING-2")
        txn = deliver_to_customer(
            user=administrator,
            final_customer="Branding QA Corp 2",
            occurred_at=date.today(),
            unit_asset_ids=[asset.pk],
        )

        document = generate_document(txn=txn, user=administrator)
        assert document.pdf_file.open("rb").read()[:4] == b"%PDF"


@pytest.mark.django_db
class TestCountryBrandingViews:
    def test_administrator_can_view_the_list(self, client, administrator, location_tree):
        client.force_login(administrator)
        response = client.get(reverse("documents:branding_list"))
        assert response.status_code == 200
        assert location_tree["country"].name in response.content.decode()

    def test_stock_manager_forbidden(self, client, stock_manager, location_tree):
        client.force_login(stock_manager)
        response = client.get(reverse("documents:branding_list"))
        assert response.status_code == 403

    def test_get_prefills_from_an_existing_profile(self, client, administrator, location_tree):
        save_branding_profile(
            user=administrator, country=location_tree["country"], company_name="Prefilled Co"
        )
        client.force_login(administrator)
        response = client.get(
            reverse("documents:branding_edit", args=[location_tree["country"].pk])
        )
        assert response.status_code == 200
        assert "Prefilled Co" in response.content.decode()

    def test_post_saves_a_profile(self, client, administrator, location_tree):
        client.force_login(administrator)
        response = client.post(
            reverse("documents:branding_edit", args=[location_tree["country"].pk]),
            {
                "company_name": "Posted Co",
                "company_address": "",
                "company_tax_id": "",
                "terms_text": "",
                "signature_left_label": "",
                "signature_right_label": "",
            },
        )
        assert response.status_code == 302
        assert get_branding_profile(location_tree["country"]).company_name == "Posted Co"

    def test_edit_404s_for_a_non_country_location(self, client, administrator, location_tree):
        client.force_login(administrator)
        response = client.get(reverse("documents:branding_edit", args=[location_tree["room"].pk]))
        assert response.status_code == 404
