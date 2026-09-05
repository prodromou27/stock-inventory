from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.services import grant_location_access
from apps.inventory.access import scope_transaction_line_queryset
from apps.inventory.models import InventoryTransactionLine
from apps.inventory.services.assignments import deliver_to_customer
from apps.inventory.services.receipts import receive_stock
from apps.reporting.models import ReportBaseModel, SavedReport
from apps.reporting.report_builder import REPORTABLE_FIELDS, build_queryset
from apps.reporting.services import create_saved_report, delete_saved_report


@pytest.fixture
def in_scope_asset(administrator, unit_product, location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=location_tree["room"],
        occurred_at=date.today(),
        vendor_serial="SN-REPORT-IN-SCOPE",
    )
    from apps.inventory.models import UnitAsset

    return UnitAsset.objects.get(vendor_serial="SN-REPORT-IN-SCOPE")


@pytest.fixture
def out_of_scope_asset(administrator, unit_product, other_location_tree):
    receive_stock(
        user=administrator,
        product=unit_product,
        location=other_location_tree["site"],
        occurred_at=date.today(),
        vendor_serial="SN-REPORT-OUT-OF-SCOPE",
    )
    from apps.inventory.models import UnitAsset

    return UnitAsset.objects.get(vendor_serial="SN-REPORT-OUT-OF-SCOPE")


@pytest.mark.django_db
class TestScopeTransactionLineQueryset:
    """apps.inventory.access.scope_transaction_line_queryset() — new,
    added specifically for the report builder's TRANSACTION_LINE base
    model (no prior caller needed it).
    """

    def test_administrator_sees_every_line(self, administrator, in_scope_asset):
        deliver_to_customer(
            user=administrator,
            final_customer="Report Test Corp",
            occurred_at=date.today(),
            unit_asset_ids=[in_scope_asset.pk],
        )
        queryset = scope_transaction_line_queryset(
            administrator, InventoryTransactionLine.objects.all()
        )
        assert queryset.filter(unit_asset=in_scope_asset).exists()

    def test_scoped_user_sees_only_granted_lines(
        self,
        administrator,
        stock_manager,
        location_tree,
        in_scope_asset,
        out_of_scope_asset,
    ):
        grant_location_access(
            user=stock_manager, location=location_tree["room"], granted_by=administrator
        )
        deliver_to_customer(
            user=administrator,
            final_customer="In Scope Corp",
            occurred_at=date.today(),
            unit_asset_ids=[in_scope_asset.pk],
        )
        deliver_to_customer(
            user=administrator,
            final_customer="Out Of Scope Corp",
            occurred_at=date.today(),
            unit_asset_ids=[out_of_scope_asset.pk],
        )
        queryset = scope_transaction_line_queryset(
            stock_manager, InventoryTransactionLine.objects.all()
        )
        assets = set(queryset.values_list("unit_asset_id", flat=True))
        assert in_scope_asset.pk in assets
        assert out_of_scope_asset.pk not in assets

    def test_user_without_any_grant_sees_nothing(self, read_only_user, in_scope_asset):
        queryset = scope_transaction_line_queryset(
            read_only_user, InventoryTransactionLine.objects.all()
        )
        assert not queryset.exists()


@pytest.mark.django_db
class TestBuildQueryset:
    def test_unit_asset_report_respects_scope(
        self,
        administrator,
        stock_manager,
        location_tree,
        in_scope_asset,
        out_of_scope_asset,
    ):
        grant_location_access(
            user=stock_manager, location=location_tree["room"], granted_by=administrator
        )
        columns, queryset = build_queryset(
            user=stock_manager,
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        serials = {row["vendor_serial"] for row in queryset}
        assert "SN-REPORT-IN-SCOPE" in serials
        assert "SN-REPORT-OUT-OF-SCOPE" not in serials

    def test_administrator_sees_everything(self, administrator, in_scope_asset, out_of_scope_asset):
        columns, queryset = build_queryset(
            user=administrator,
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        serials = {row["vendor_serial"] for row in queryset}
        assert "SN-REPORT-IN-SCOPE" in serials
        assert "SN-REPORT-OUT-OF-SCOPE" in serials

    def test_transaction_report_respects_scope(
        self,
        administrator,
        stock_manager,
        location_tree,
        in_scope_asset,
        out_of_scope_asset,
    ):
        grant_location_access(
            user=stock_manager, location=location_tree["room"], granted_by=administrator
        )
        deliver_to_customer(
            user=administrator,
            final_customer="In Scope Corp",
            occurred_at=date.today(),
            unit_asset_ids=[in_scope_asset.pk],
        )
        deliver_to_customer(
            user=administrator,
            final_customer="Out Of Scope Corp",
            occurred_at=date.today(),
            unit_asset_ids=[out_of_scope_asset.pk],
        )
        columns, queryset = build_queryset(
            user=stock_manager,
            base_model=ReportBaseModel.TRANSACTION,
            selected_fields=["final_customer"],
            filters=[],
        )
        customers = {row["final_customer"] for row in queryset}
        assert "In Scope Corp" in customers
        assert "Out Of Scope Corp" not in customers

    def test_unknown_selected_field_is_dropped_not_raised(self, administrator, in_scope_asset):
        columns, queryset = build_queryset(
            user=administrator,
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial", "not-a-real-field", "__class__"],
            filters=[],
        )
        assert columns == ["serial"]
        list(queryset)  # doesn't raise

    def test_empty_selected_fields_falls_back_to_every_field(self, administrator):
        columns, queryset = build_queryset(
            user=administrator,
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=[],
            filters=[],
        )
        assert set(columns) == set(REPORTABLE_FIELDS[ReportBaseModel.UNIT_ASSET])

    def test_unknown_filter_field_is_dropped_not_raised(self, administrator, in_scope_asset):
        columns, queryset = build_queryset(
            user=administrator,
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[{"field_key": "not-a-real-field__isnull", "op": "exact", "value": "x"}],
        )
        serials = {row["vendor_serial"] for row in queryset}
        assert "SN-REPORT-IN-SCOPE" in serials  # filter had no effect, not an error

    def test_disallowed_op_is_dropped_not_raised(self, administrator, in_scope_asset):
        columns, queryset = build_queryset(
            user=administrator,
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[{"field_key": "serial", "op": "regex", "value": ".*"}],
        )
        serials = {row["vendor_serial"] for row in queryset}
        assert "SN-REPORT-IN-SCOPE" in serials  # filter had no effect, not an error

    def test_icontains_filter_narrows_results(self, administrator, in_scope_asset):
        columns, queryset = build_queryset(
            user=administrator,
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[{"field_key": "serial", "op": "icontains", "value": "IN-SCOPE"}],
        )
        serials = {row["vendor_serial"] for row in queryset}
        assert serials == {"SN-REPORT-IN-SCOPE"}

    def test_in_filter_splits_comma_separated_values(self, administrator, in_scope_asset):
        columns, queryset = build_queryset(
            user=administrator,
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[
                {
                    "field_key": "serial",
                    "op": "in",
                    "value": "SN-REPORT-IN-SCOPE, SN-DOES-NOT-EXIST",
                }
            ],
        )
        serials = {row["vendor_serial"] for row in queryset}
        assert serials == {"SN-REPORT-IN-SCOPE"}

    def test_every_base_model_produces_a_runnable_queryset(self, administrator):
        for base_model in ReportBaseModel.values:
            columns, queryset = build_queryset(
                user=administrator, base_model=base_model, selected_fields=[], filters=[]
            )
            assert columns
            list(queryset)  # doesn't raise for any of the five models

    def test_invalid_legacy_typed_filter_is_ignored_instead_of_raising_500(
        self, administrator, in_scope_asset
    ):
        columns, queryset = build_queryset(
            user=administrator,
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[{"field_key": "arrival_date", "op": "gte", "value": "not-a-date"}],
        )

        assert columns == ["serial"]
        assert list(queryset)


@pytest.mark.django_db
class TestCreateSavedReport:
    def test_any_authenticated_user_can_create_unshared_report(self, read_only_user):
        report = create_saved_report(
            user=read_only_user,
            name="My assets",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial", "brand"],
            filters=[],
        )
        assert report.is_shared is False
        assert report.created_by == read_only_user

    def test_non_administrator_cannot_share(self, stock_manager):
        report = create_saved_report(
            user=stock_manager,
            name="Attempted share",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
            is_shared=True,
        )
        assert report.is_shared is False

    def test_administrator_can_share(self, administrator):
        report = create_saved_report(
            user=administrator,
            name="Shared report",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
            is_shared=True,
        )
        assert report.is_shared is True

    def test_blank_name_rejected(self, administrator):
        with pytest.raises(ValidationError):
            create_saved_report(
                user=administrator,
                name="  ",
                base_model=ReportBaseModel.UNIT_ASSET,
                selected_fields=["serial"],
                filters=[],
            )

    def test_unknown_base_model_rejected(self, administrator):
        with pytest.raises(ValidationError):
            create_saved_report(
                user=administrator,
                name="Bad report",
                base_model="not-a-real-model",
                selected_fields=["serial"],
                filters=[],
            )

    def test_no_valid_fields_rejected(self, administrator):
        with pytest.raises(ValidationError):
            create_saved_report(
                user=administrator,
                name="No fields",
                base_model=ReportBaseModel.UNIT_ASSET,
                selected_fields=["not-a-real-field"],
                filters=[],
            )

    def test_filters_are_cleaned_of_unrecognized_entries(self, administrator):
        report = create_saved_report(
            user=administrator,
            name="Mixed filters",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[
                {"field_key": "serial", "op": "icontains", "value": "SN"},
                {"field_key": "not-a-real-field", "op": "exact", "value": "x"},
                {"field_key": "serial", "op": "not-a-real-op", "value": "x"},
                {"field_key": "serial", "op": "exact", "value": ""},
            ],
        )
        assert report.filters == [{"field_key": "serial", "op": "icontains", "value": "SN"}]

    def test_invalid_typed_filter_is_rejected_before_report_is_saved(self, administrator):
        with pytest.raises(ValidationError, match="not a valid value"):
            create_saved_report(
                user=administrator,
                name="Invalid date",
                base_model=ReportBaseModel.UNIT_ASSET,
                selected_fields=["serial"],
                filters=[{"field_key": "arrival_date", "op": "gte", "value": "not-a-date"}],
            )

    def test_contains_filter_is_rejected_for_non_text_field(self, administrator):
        with pytest.raises(ValidationError, match="text fields"):
            create_saved_report(
                user=administrator,
                name="Invalid operator",
                base_model=ReportBaseModel.STOCK_BALANCE,
                selected_fields=["on_hand_quantity"],
                filters=[{"field_key": "on_hand_quantity", "op": "icontains", "value": "1"}],
            )


@pytest.mark.django_db
class TestDeleteSavedReport:
    def test_owner_can_delete(self, stock_manager):
        report = create_saved_report(
            user=stock_manager,
            name="Mine",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        delete_saved_report(report=report, user=stock_manager)
        assert not SavedReport.objects.filter(pk=report.pk).exists()

    def test_non_owner_cannot_delete(self, stock_manager, read_only_user):
        report = create_saved_report(
            user=stock_manager,
            name="Mine",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        with pytest.raises(PermissionDenied):
            delete_saved_report(report=report, user=read_only_user)
        assert SavedReport.objects.filter(pk=report.pk).exists()

    def test_administrator_can_delete_anyones_report(self, administrator, stock_manager):
        report = create_saved_report(
            user=stock_manager,
            name="Mine",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        delete_saved_report(report=report, user=administrator)
        assert not SavedReport.objects.filter(pk=report.pk).exists()


@pytest.mark.django_db
class TestReportBuilderViews:
    def test_anonymous_redirected(self, client):
        assert client.get(reverse("reporting:saved_report_list")).status_code == 302
        assert client.get(reverse("reporting:builder_start")).status_code == 302

    def test_builder_start_redirects_to_builder_with_base_model(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.post(
            reverse("reporting:builder_start"), {"base_model": ReportBaseModel.UNIT_ASSET}
        )
        assert response.status_code == 302
        assert "base_model=unit_asset" in response["Location"]

    def test_builder_get_without_base_model_redirects_to_start(self, client, read_only_user):
        client.force_login(read_only_user)
        response = client.get(reverse("reporting:builder"))
        assert response.status_code == 302
        assert response["Location"] == reverse("reporting:builder_start")

    def test_builder_post_creates_report_and_redirects_to_run(self, client, stock_manager):
        client.force_login(stock_manager)
        response = client.post(
            f"{reverse('reporting:builder')}?base_model=unit_asset",
            {
                "base_model": "unit_asset",
                "name": "Serials report",
                "selected_fields": ["serial", "brand"],
                "form-TOTAL_FORMS": "3",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        report = SavedReport.objects.get(name="Serials report")
        assert response["Location"] == reverse("reporting:saved_report_run", args=[report.pk])
        assert report.created_by == stock_manager

    def test_run_view_shows_scoped_results(
        self,
        client,
        administrator,
        stock_manager,
        location_tree,
        in_scope_asset,
        out_of_scope_asset,
    ):
        grant_location_access(
            user=stock_manager, location=location_tree["room"], granted_by=administrator
        )
        report = create_saved_report(
            user=stock_manager,
            name="My assets",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        client.force_login(stock_manager)
        response = client.get(reverse("reporting:saved_report_run", args=[report.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "SN-REPORT-IN-SCOPE" in content
        assert "SN-REPORT-OUT-OF-SCOPE" not in content

    def test_private_report_is_404_for_another_user(self, client, stock_manager, read_only_user):
        report = create_saved_report(
            user=stock_manager,
            name="Private",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        client.force_login(read_only_user)
        response = client.get(reverse("reporting:saved_report_run", args=[report.pk]))
        assert response.status_code == 404

    def test_shared_report_is_visible_to_another_user(self, client, administrator, read_only_user):
        report = create_saved_report(
            user=administrator,
            name="Shared",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
            is_shared=True,
        )
        client.force_login(read_only_user)
        response = client.get(reverse("reporting:saved_report_run", args=[report.pk]))
        assert response.status_code == 200

    def test_csv_export(self, client, administrator, in_scope_asset):
        report = create_saved_report(
            user=administrator,
            name="CSV test",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        client.force_login(administrator)
        response = client.get(
            reverse("reporting:saved_report_run", args=[report.pk]), {"format": "csv"}
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert "SN-REPORT-IN-SCOPE" in response.content.decode()

    def test_run_view_is_paginated(self, client, administrator, unit_product, location_tree):
        from apps.inventory.models import UnitAsset, UnitStatus

        UnitAsset.objects.bulk_create(
            [
                UnitAsset(
                    product=unit_product,
                    vendor_serial=f"SN-PAGE-{index:03d}",
                    status=UnitStatus.IN_STOCK,
                    current_location=location_tree["room"],
                    arrival_date=date.today(),
                )
                for index in range(55)
            ]
        )
        report = create_saved_report(
            user=administrator,
            name="Paginated",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        client.force_login(administrator)

        response = client.get(reverse("reporting:saved_report_run", args=[report.pk]))

        assert response.status_code == 200
        assert len(response.context["rows"]) == 50
        assert response.context["page_obj"].paginator.num_pages == 2

    def test_saved_sort_order_is_applied(self, client, administrator, in_scope_asset):
        report = create_saved_report(
            user=administrator,
            name="Descending serials",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
            sort_by="serial",
            sort_direction="desc",
        )
        client.force_login(administrator)

        response = client.get(reverse("reporting:saved_report_run", args=[report.pk]))

        serials = [row[0] for row in response.context["rows"]]
        assert serials == sorted(serials, reverse=True)

    def test_xlsx_export(self, client, administrator, in_scope_asset):
        import io

        import openpyxl

        report = create_saved_report(
            user=administrator,
            name="Excel test",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        client.force_login(administrator)

        response = client.get(
            reverse("reporting:saved_report_run", args=[report.pk]), {"format": "xlsx"}
        )

        assert response.status_code == 200
        workbook = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True)
        assert workbook.active["A1"].value == "serial"
        assert workbook.active["A2"].value == "SN-REPORT-IN-SCOPE"

    def test_pdf_export(self, client, administrator, in_scope_asset):
        report = create_saved_report(
            user=administrator,
            name="PDF test",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        client.force_login(administrator)

        response = client.get(
            reverse("reporting:saved_report_run", args=[report.pk]), {"format": "pdf"}
        )

        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")

    def test_numeric_totals_cover_full_result(
        self, client, administrator, quantity_product, location_tree
    ):
        receive_stock(
            user=administrator,
            product=quantity_product,
            location=location_tree["room"],
            occurred_at=date.today(),
            quantity=7,
        )
        report = create_saved_report(
            user=administrator,
            name="Balance totals",
            base_model=ReportBaseModel.STOCK_BALANCE,
            selected_fields=["on_hand_quantity", "reserved_quantity"],
            filters=[],
        )
        client.force_login(administrator)

        response = client.get(reverse("reporting:saved_report_run", args=[report.pk]))

        assert response.status_code == 200
        assert response.context["totals"] == [7, 0]

    def test_legacy_report_with_invalid_typed_filter_does_not_return_500(
        self, client, administrator, in_scope_asset
    ):
        report = SavedReport.objects.create(
            name="Legacy invalid date",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[{"field_key": "arrival_date", "op": "gte", "value": "not-a-date"}],
            created_by=administrator,
            updated_by=administrator,
        )
        client.force_login(administrator)

        response = client.get(reverse("reporting:saved_report_run", args=[report.pk]))

        assert response.status_code == 200
        assert "SN-REPORT-IN-SCOPE" in response.content.decode()

    def test_list_view_shows_own_and_shared_not_others_private(
        self, client, administrator, stock_manager, read_only_user
    ):
        create_saved_report(
            user=stock_manager,
            name="SM private",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        create_saved_report(
            user=administrator,
            name="Admin shared",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
            is_shared=True,
        )
        client.force_login(read_only_user)
        response = client.get(reverse("reporting:saved_report_list"))
        content = response.content.decode()
        assert "Admin shared" in content
        assert "SM private" not in content

    def test_non_owner_cannot_delete_via_view(self, client, stock_manager, read_only_user):
        report = create_saved_report(
            user=stock_manager,
            name="Mine",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        client.force_login(read_only_user)
        response = client.post(reverse("reporting:saved_report_delete", args=[report.pk]))
        assert response.status_code == 404
        assert SavedReport.objects.filter(pk=report.pk).exists()

    def test_owner_can_delete_via_view(self, client, stock_manager):
        report = create_saved_report(
            user=stock_manager,
            name="Mine",
            base_model=ReportBaseModel.UNIT_ASSET,
            selected_fields=["serial"],
            filters=[],
        )
        client.force_login(stock_manager)
        response = client.post(reverse("reporting:saved_report_delete", args=[report.pk]))
        assert response.status_code == 302
        assert not SavedReport.objects.filter(pk=report.pk).exists()
