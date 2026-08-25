# Traceability Matrix — Final Release Audit (Prompt 9)

Maps every acceptance criterion in spec §21 and every explicitly out-of-scope item in spec §22 to the code and
tests that satisfy (or, for §22, deliberately don't implement) it. Built at the end of the delivery backlog
(`09-delivery-backlog.md`) as the final acceptance gate spec §23.1 calls for — every criterion below is backed by a
real, currently-passing test, not just a design intention.

As of this audit: **384 tests pass**, `ruff check .` and `black --check .` are clean, `manage.py makemigrations
--check --dry-run` reports no drift, and GitHub Actions CI (`.github/workflows/ci.yml`) runs all of the above
against a real PostgreSQL 16 instance on every push — [confirmed green](https://github.com/prodromou27/stock-inventory/actions).

## §21 — Minimum acceptance criteria

### 1. A Stock Manager can receive serialized and quantity-based stock

- Code: `apps/inventory/services/receipts.py::receive_stock`, `_receive_unit`, `_receive_quantity`
- Tests: `tests/test_inventory_receipts.py::TestReceiveUnitStock::test_receipt_creates_unit_asset_in_stock`,
  `::TestReceiveQuantityStock::test_receipt_increments_stock_balance`

### 2. Two different unit assets may share a vendor serial only after a visible acknowledgement

- Code: `apps/inventory/services/duplicates.py::check_duplicate_serial`;
  `receipts.py::DuplicateSerialError`, `receive_stock(duplicate_serial_acknowledged=...)`
- Tests: `tests/test_inventory_receipts.py::TestReceiveUnitStock::test_duplicate_serial_blocks_without_acknowledgement`,
  `::test_duplicate_serial_allowed_with_acknowledgement_and_audited`,
  `::test_serials_are_allowed_to_repeat_no_db_uniqueness`

### 3. A user cannot see records outside authorized storage scopes in any interface or export

- Code: `apps/locations/scoping.py::accessible_locations`, `scope_queryset`, `require_location_access`, applied in
  every list/detail/export view (`apps/inventory/views.py::UnitAssetListView`/`StockBalanceListView`, both using
  `CSVExportMixin`) and every report query (`apps/reporting/queries.py::_scoped_assets`/`_scoped_balances`)
- Tests: `tests/test_scoping.py::test_grant_cascades_to_descendants_only`, `::test_out_of_scope_location_denied`;
  `tests/test_inventory_views.py::test_list_scoped_to_accessible_locations`, `::test_detail_denied_outside_scope`;
  `tests/test_inventory_filters.py::test_filters_are_scoped_first`;
  `tests/test_reporting.py::test_damaged_assets_report_scoped_and_filtered`, `::test_excludes_out_of_scope_units`;
  `tests/test_inventory_transaction_access.py::test_denies_assignment_transaction_outside_scope`

### 4. Multiple assets can be transferred between storage locations in one transaction

- Code: `apps/inventory/services/transfers.py::bulk_transfer`
- Tests: `tests/test_inventory_transfers.py::test_mixed_unit_and_quantity_lines_in_one_transaction`,
  `::test_transfers_unit_asset_keeping_status`, `::test_transfers_quantity_between_balances`

### 5. Stock can be reserved against a manually entered Project Reference and Final Customer

- Code: `apps/inventory/services/reservations.py::reserve_stock`; `StockReservation.project_reference`/
  `final_customer` (`apps/inventory/models.py`)
- Tests: `tests/test_inventory_reservations.py::test_project_reference_required`,
  `::test_unit_reservation_sets_status_reserved`,
  `::test_quantity_reservation_creates_reservation_row_and_reserves_balance`

### 6. One assignment/delivery can contain multiple unit and quantity lines

- Code: `apps/inventory/services/assignments.py::assign_to_employee`, `deliver_to_customer`
- Tests: `tests/test_inventory_assignments_deliveries.py::test_mixed_multi_line_assignment`

### 7. A partial return leaves outstanding items assigned/delivered

- Code: `apps/inventory/services/returns.py::return_stock`
- Tests: `tests/test_inventory_returns.py::test_partial_return_leaves_other_lines_assigned`

### 8. Condition and accessories can be recorded at issue and return

- Code: `apps/inventory/services/ledger.py::write_unit_line` (`condition`/`accessories` params);
  `returns.py::return_stock` (`condition`/`accessories` params, threaded to the same line writer);
  `InventoryTransactionLine.condition_snapshot`/`accessories_snapshot`
- Tests: `tests/test_inventory_assignments_deliveries.py::test_condition_and_accessories_captured_on_line` (issue
  side); `tests/test_inventory_returns.py::test_condition_and_accessories_captured_on_return` (return side — added
  during this audit: the service already threaded these through correctly, but had no dedicated test on the return
  side until now)

### 9. Removal Date is preserved for every event where inventory physically leaves storage

- Code: `apps/inventory/services/ledger.py::write_unit_line` (sets `asset.last_removal_date` whenever
  `to_location is None`); `UnitAsset.last_removal_date`
- Tests: `tests/test_inventory_assignments_deliveries.py::test_unit_assignment_removes_from_storage`;
  `tests/test_inventory_disposition.py::test_marks_lost_and_clears_location`

### 10. Disposed HDDs remain searchable and appear in the disposed-items report

- Code: `apps/inventory/services/disposition.py::dispose`; `apps/reporting/queries.py::disposed_items`
- Tests: `tests/test_inventory_disposition.py::test_disposed_hdd_remains_searchable`;
  `tests/test_reporting.py::test_disposed_items_report_includes_hdd_and_survives_after_disposal`,
  `::test_disposed_items_type_filter`

### 11. Stock Managers complete movements without secondary approval

- Code: `apps/core/authorization.py::RoleRequiredMixin`/`require_role` (every movement view allows both
  Administrator and StockManager); every movement service commits its `InventoryTransaction` synchronously —
  `InventoryTransaction` has no pending/approval-status field anywhere in the model or `apps/inventory/transitions.py`
- Tests: `tests/test_inventory_movement_views.py::TestMovementsHubAccess::test_stock_manager_allowed`,
  `::TestTransferView::test_full_flow`

### 12. Administrators can correct mistakes without erasing the original audit history

- Code: `apps/inventory/services/corrections.py::correct_unit_status`, `correct_balance`, `reverse_transaction`;
  `apps/core/models.py::AppendOnlyModel`/`AppendOnlyQuerySet` (blocks update/delete at the ORM layer for every
  ledger/audit table)
- Tests: `tests/test_inventory_corrections.py::test_administrator_can_force_any_status`,
  `::test_original_transaction_is_never_mutated`, `::test_reverses_disposal`, `::test_cannot_reverse_twice`;
  `tests/test_inventory_receipts.py::TestLedgerImmutability::test_transaction_cannot_be_updated_after_creation`,
  `::test_bulk_update_and_delete_blocked_on_transactions`

### 13. Printable PDFs are immutable snapshots and optional signed copies can be uploaded

- Code: `apps/documents/services.py::generate_document`, `regenerate_document` (via `supersedes`),
  `upload_attachment`; `GeneratedDocument` (`AppendOnlyModel` + `supersedes` self-FK), `Attachment`
- Tests: `tests/test_documents_services.py::test_document_cannot_be_updated_after_creation`,
  `::test_pdf_file_unaffected_by_later_product_edit`, `::test_creates_new_row_linked_via_supersedes`,
  `::test_original_pdf_file_untouched`, `::test_uploads_valid_pdf`,
  `::test_second_upload_creates_new_row_never_overwrites`

### 14. The Excel importer previews errors and does not duplicate rows when safely retried

- Code: `apps/imports/services.py::create_batch_from_upload`, `_stage_row` (preview/validation),
  `execute_batch`, `_execute_row` (idempotent retry — only `pending`/`warning` rows are re-attempted)
- Tests: `tests/test_imports_services.py::test_idempotent_on_retry`, `::test_duplicate_serial_is_warning`,
  `::test_duplicate_product_is_warning`, `::test_tracking_method_conflict_is_failed`,
  `::test_executes_pending_rows_and_dedupes_products`

### 15. All lists are paginated and remain responsive with at least 8,000 imported records

- Code: `apps/inventory/views.py::UnitAssetListView`/`StockBalanceListView`/`TransactionListView` (`paginate_by`);
  `apps/reporting/views.py::CurrentStockView`/`ReservedStockView`/`StockByProjectReferenceView` (manual
  `Paginator`, added during Prompt 8 after real timing measurement found them unpaginated);
  `apps/inventory/management/commands/seed_bulk_inventory.py` (the 8,000+-row fixture used to measure this for real)
- Tests: `tests/test_performance.py::test_list_is_paginated_not_loading_everything`,
  `::test_list_query_count_does_not_scale_with_row_count`, `::test_filtered_list_still_bounded_query_count`,
  `::test_csv_export_returns_all_matching_rows_not_one_page`;
  `tests/test_reporting.py::TestCurrentStockReport::test_units_are_paginated`,
  `TestReservedStockReport::test_units_are_paginated`. Real wall-clock timings (not just query counts) recorded in
  doc 09's Prompt 8 entry: ~60–150ms per paginated list/report page against the 8,000+-row dataset.

## §22 — Explicitly out of scope

Confirmed absent by direct search of the codebase (`grep`), not merely "never mentioned in a design doc" — each
item below was actively checked for and found genuinely unimplemented, matching the spec's explicit exclusion.

| # | Item | Confirmation |
|---|---|---|
| 1 | CRM integration or synchronization | No CRM client, API integration, or sync job anywhere in `apps/` |
| 2 | Automatic validation of Project References | `project_reference` is a plain `CharField` everywhere it appears (`UnitAsset`, `StockBalance`-adjacent `InventoryTransaction`, `StockReservation`) — free text, never checked against an external system |
| 3 | Customer/project master-data management | No `Customer` or `Project` model exists; `final_customer`/`project_reference` are plain text fields on transactions/assets, not foreign keys to a managed master record |
| 4 | Barcode/QR scanning and label printing | No barcode/QR library in `requirements.txt`; no scanning/label-printing view or template |
| 5 | Electronic signatures | `Attachment` (doc 02/06) supports uploading a *scanned* signed copy as a file — no e-signature capture/consent flow of any kind |
| 6 | Mobile-specific application | Single server-rendered Django app; no separate mobile client or mobile-only API |
| 7 | Offline operation | No service worker, no offline-first design; every screen requires a live connection to the server |
| 8 | Automated overdue notifications | `apps/reporting/queries.py`'s `temporary_assignments()` docstring states explicitly: "No overdue automation (spec §9/§16) — expected_return_date is shown [for a human to check], not proactively alerted on" |
| 9 | Mandatory minimum-stock alerts | `Product.low_stock_threshold` is nullable and opt-in per product; the low-stock report/dashboard is explicitly "disabled unless configured" (spec §16, doc 09's Phase 7 entry) — not automatic or mandatory |
| 10 | Customer addresses and contacts | `final_customer` is a plain text company-name field; no address/contact sub-model |
| 11 | Secondary approval workflow | Every movement service (`receive_stock`, `bulk_transfer`, `assign_to_employee`, etc.) writes its `InventoryTransaction` and returns — no pending/approval status exists anywhere in `apps/inventory/transitions.py`'s state model |

## Known gaps and deliberate scope simplifications (not §21/§22 items, but worth recording here)

- **Browser-level (Playwright/Selenium) tests** from doc 08's original Testing plan were not built. Every critical
  multi-step workflow was instead verified via the Django test client (`pytest`) plus a live HTTP smoke test
  against a running dev server at the end of each phase — see doc 08's Testing section for the full reasoning.
- **User creation and role assignment** has no dedicated in-app screen — it's handled by Django's built-in
  `/admin/` site (see the new administrator quick-start guide, `docs/administrator-quickstart.md`). Only the
  location-scoped access grant/revoke (the part needing custom audited business logic) has a purpose-built screen.
  `apps/accounts/signals.py`'s `sync_is_staff_with_administrator_group` (added during this audit) keeps every
  Administrator able to reach `/admin/` for this, not just the original `createsuperuser` account.
- **Production Docker Compose topology** (`deploy/docker-compose.prod.yml`, `Dockerfile.prod`, nginx) has not been
  booted end to end via Docker itself — the development machine this was built on doesn't have Docker installed.
  Every piece *within* that topology was verified directly against real PostgreSQL instead (see
  `deploy/DEPLOYMENT.md`'s verification note and doc 09's Prompt 8 entry) — smoke-testing the actual compose
  topology once is called out there as a pre-cutover step.
- **Tracking-method migration** for a product with live stock (doc 10, open item #8) isn't implemented — only the
  lock that makes it necessary is. A product's tracking method can only be changed while it has zero movements.
- **Multi-quantity-line UI**: each movement screen accepts any number of unit-asset checkboxes but only a single
  quantity line per submission (doc 09's Phase 4 entry) — the underlying services accept full lists and are tested
  that way; the UI just doesn't expose a dynamic add-row control for quantity lines yet.

## Security review

Conducted as part of this audit (Prompt 8 already covered login throttling, session/CSRF/password policy, upload
validation, DB-level defense in depth, and secrets handling — see doc 08 and doc 09's Prompt 8 entry; this pass
covered the rest):

- No raw SQL built from user input anywhere (`grep`-confirmed: the only `cursor.execute()` calls are hardcoded
  `nextval()`/`SELECT 1` statements with no interpolated values) — no SQL injection surface.
- No `mark_safe()`, `|safe` template filter, or `{% autoescape off %}` anywhere — Django's default HTML
  auto-escaping is intact on every template; no `<script>` tag anywhere renders a template variable into a JS
  context. No `eval`/`exec`/`pickle`/unsafe `yaml.load`/`subprocess`/`os.system` anywhere in `apps/`.
- Full git history scanned for committed secrets, credential files, or private keys before the repository was
  pushed to GitHub — none found; only `.env.example`/`.env.production.example` (placeholder values) are tracked.
- Django's `manage.py check --deploy` against the real production settings module reports a clean bill of health
  (the one remaining warning, `SECURE_HSTS_PRELOAD`, is a deliberate opt-in — see doc 09's Prompt 8 entry).
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `SESSION_COOKIE_HTTPONLY`, and
  `SESSION_COOKIE_SAMESITE=Lax` all confirmed active.
- **One real finding, fixed**: only the original `createsuperuser` account could reach Django's built-in `/admin/`
  site — which is how a new user account gets created and assigned a role at all (see "Known gaps" above) — since
  nothing kept `is_staff` in sync with the app's own Administrator role. Fixed with
  `apps/accounts/signals.py::sync_is_staff_with_administrator_group`; see `tests/test_admin_staff_sync.py`. Not a
  privilege-escalation risk (every view still checks the Administrator group directly, never `is_staff` alone) but
  a genuine operational lockout risk for a real deployment creating its second Administrator.
- No dependency vulnerability scan (`pip-audit`/`safety`) was run — no such tool was available in this
  environment. `requirements.txt` pins reasonably current major versions with upper bounds (Django 5.x, psycopg
  3.x, gunicorn 21.x, WeasyPrint 69.x, openpyxl 3.x, django-axes 8.x); recommend running `pip-audit` as a routine
  pre-deploy and periodic operational check, not a one-time gate.

## Release recommendation

**Ready for production deployment**, with three items to close out first (none are code changes — all are
operational steps already documented but not yet exercised in a real target environment):

1. **Boot the actual Docker Compose production topology once** in a real environment before first cutover
   (`deploy/docker-compose.prod.yml`) — everything inside it was verified independently against real PostgreSQL,
   but the compose file itself, `Dockerfile.prod`, and the nginx reverse proxy have not been booted together via
   Docker (this development machine doesn't have Docker installed). See `deploy/DEPLOYMENT.md`'s verification note.
2. **Provision the runtime-role DB hardening** (`deploy/sql/hardening_runtime_role.sql`) and confirm
   `RUNTIME_DB_USER`/`RUNTIME_DB_PASSWORD` are set before go-live — optional but recommended defense in depth,
   verified working against real PostgreSQL, just not yet wired into an actual deployed environment's `.env`.
3. **Practice the restore procedure once more against that real target environment's infrastructure**
   (`deploy/RESTORE.md`) — already executed successfully once against the local dev database; re-running it in the
   actual production-equivalent environment before go-live is the standard "don't trust a backup you haven't
   restored" practice, not a sign anything is currently broken.

Everything else — every §21 acceptance criterion, every §22 exclusion, the full security review, and the CI
pipeline — is done, verified, and green as of this commit.
