# Phased Delivery Backlog

Expands spec §20's four phases into concrete backlog items with dependencies and acceptance criteria, aligned with
the prompt pack's Prompts 1–9 (each prompt below = one reviewed increment; per spec §23.1, none of these are run as
one unattended change).

## Phase 1 — Foundation (prompt pack Prompt 1) — done

| Item | Depends on |
|---|---|
| Django project skeleton, settings split (base/dev/test/production) | — |
| PostgreSQL service + `ltree` extension enabled via migration | — |
| Docker Compose dev environment, Dockerfile, health/readiness endpoint | Django skeleton |
| Local username/password auth, base layout/navigation shell | Django skeleton |
| pytest/pytest-django, formatter, static-analysis config | Django skeleton |
| Structured logging | Django skeleton |
| Seed command: one user per role, no hard-coded production passwords | Auth |
| `CLAUDE.md`/`AGENTS.md` | all of the above (documents the real commands) |

**Acceptance**: `docker compose up` boots the stack; `manage.py migrate` succeeds; a seeded user of each role can
log in; `pytest` runs green in CI-equivalent form; production settings module refuses to run with `DEBUG=True` or a
missing `SECRET_KEY`.

## Phase 2 — Inventory operations

### Prompt 2 — Locations, users, scoped permissions — done

Depends on Phase 1. Delivers: the `audit` app (`AuditEvent`, `record_event()`, append-only enforcement — this was
listed under spec §20's Phase 1 but not carried into this backlog's Phase 1 table; built here instead, as the first
thing Prompt 2 needs), `Location` model + hierarchy validation trigger, `UserLocationAccess`, the three Groups
(created in Phase 1), the `apps.core.authorization` / `apps.locations.scoping` layer (see doc 04's note on why this
ended up as two modules rather than one `core.scoping`), location list/detail/create/deactivate screens, a
user-access management screen, login success/failure audit signals, and audit events for every location/access
change.

**Acceptance**: acceptance criterion §21.3 test suite (direct-URL scope bypass attempts fail) passes; deactivating
a referenced location keeps it visible in history; seed data (`seed_locations` management command) includes a
realistic one-country/one-building/second-floor/room/rack/shelf tree.

### Prompt 3 — Product catalog and inventory ledger — done

Depends on Prompt 2 (needs `Location`, scoping, roles). Delivered: `apps.catalog` (`Brand`/`ProductType` as
get-or-create lookups from free-text input, `Product`, `check_duplicate_products()`, the tracking-method lock in
`update_product()`); `apps.inventory` (`UnitAsset`, `AssetStatusHistory`, `StockBalance`,
`InventoryTransaction`/`InventoryTransactionLine`, `services/ledger.py` as the sole writer, `services/receipts.py`'s
`receive_stock()` for both tracking methods, `services/duplicates.py`'s scope-aware `check_duplicate_serial()`);
list/detail views for products, assets, stock balances, and transactions, plus the receive-stock form with the
duplicate-serial acknowledgement flow. Also promoted the append-only save()/delete() guard from `apps.audit` into a
shared `apps.core.models.AppendOnlyModel`/`AppendOnlyQuerySet` mixin, reused by `InventoryTransaction`,
`InventoryTransactionLine`, and `AssetStatusHistory`.

`receive_stock()` writes one line per call (not the multi-line-per-transaction shape doc 02 describes for
InventoryTransaction generally) — multi-line transactions are deferred to Prompt 4, which is where the spec actually
requires them (assignment/delivery), so that's where the multi-line UI/service pattern gets built. `ProductService.
migrate_tracking_method()` (doc 02) was **not** implemented — only the lock that makes it necessary was; the
migration operation itself remains open per doc 10's open item #8.

**Acceptance**: acceptance criteria §21.1 (receive both tracking types) and §21.2 (duplicate serial acknowledgement
visible and audited) — verified by tests and a live end-to-end run against real PostgreSQL; tracking-method lock
enforced by test.

### Prompt 4 — Movement workflows — done

Depends on Prompt 3. Delivered all twelve movement services (`apps/inventory/services/`): `transfers.bulk_transfer()`,
`reservations.reserve_stock()`/`release_reservation()`, `assignments.assign_to_employee()`/`deliver_to_customer()`
(sharing an internal `_issue_stock()`), `returns.return_stock()`/`assess_return()`, `disposition.mark_damaged()`/
`mark_lost()`/`dispose()`, and `corrections.correct_unit_status()`/`correct_balance()`/`reverse_transaction()`
(Administrator-only). `services/ledger.py` grew shared `write_unit_line()`/`write_quantity_line()`/`adjust_balance()`/
`adjust_reserved()` primitives that every one of these builds on — receipts.py's create-a-new-asset path stayed on
its own hand-written lines (deliberately: receiving has no "from" state to transition out of, so it doesn't fit the
shared transition-based helpers). `StockReservation` (deferred from Phase 3) was added here, where it's first used.
Also added `apps/inventory/transitions.py`, making the status-transition table (doc 03) into an enforced function
every service calls before writing anything.

Every workflow got a UI screen (checkbox asset picker + a single optional quantity line + the workflow's own fields
— see `templates/inventory/`), reachable from a new "Movements" hub page rather than growing the top nav further.
Administrator correction/reversal live on the asset/balance/transaction detail pages, gated by `user_is_administrator`.

**Scope simplifications, deliberate:**
- The UI exposes exactly one quantity line per submission (any number of unit-asset checkboxes, but a single
  product/location/quantity for the quantity side) — the services underneath accept full lists and are tested
  that way; a true multi-quantity-line UI (dynamic add-row) wasn't built to keep this already-large phase bounded.
- Reservation *consumption* during assignment/delivery (drawing from an existing `StockReservation` rather than
  general available stock) isn't wired into the UI — `StockReservation.consuming_transaction` exists in the schema
  for this but nothing sets it yet.
- `return_stock()` doesn't cap a quantity return at what was originally issued minus what's already been returned
  on that transaction — a partial-then-partial-again quantity return isn't validated against the original amount.
- Damaged -> In Stock ("after repair," spec §8) is reachable only through an Administrator correction, not a
  dedicated "repair" workflow — the prompt pack's twelve named workflows don't include one.

**Two real bugs found and fixed while testing** (both confirmed by a failing test before the fix, not just found by
inspection): the transition table originally included `IN_STOCK -> IN_STOCK` and `RESERVED -> RESERVED` self-loops
to let transfer keep an asset's status unchanged while its location moves. Reusing that same table for reservation
and return-assessment made "reserve an already-reserved asset" and "assess an asset that was never returned" both
incorrectly succeed, since both compare a status against itself. Fixed by giving transfer its own
`validate_transferable()` check instead of overloading the shared transition table — see `transitions.py`.

**Acceptance**: acceptance criteria §21.4–§21.9, §21.11, §21.12 — verified by 207 passing tests (up from 130) and a
live end-to-end run of every service, then every new view, against real PostgreSQL. The full status-transition
table (doc 03) is covered by tests including invalid-transition rejection, insufficient-quantity rejection, and
scope-violation rejection for every workflow.

## Phase 3 — Documents and reporting

### Prompt 5 — Printable forms, PDFs, attachments — done

Depends on Prompt 4 (needs completed assignment/delivery transactions to render). Delivered `apps.documents`:
`GeneratedDocument`/`Attachment` models, `services.generate_document()`/`regenerate_document()` (WeasyPrint
rendering from `InventoryTransactionLine` snapshots via `pdf.py`, one shared `templates/documents/pdf/form_v1.html`
covering both assignment and delivery), `services.upload_attachment()`/`delete_attachment()` (magic-byte content
sniffing rather than trusting the client, storage filenames derived from the row's own UUID rather than the
uploaded filename), and download views that stream files directly (never through a public media path). Wired into
the transaction detail page (generate/regenerate, upload, list, admin-only delete).

**A real access-control gap was found and fixed while building this**, before any document/attachment code
depended on it: the existing `TransactionDetailView` scope check only looked at `InventoryTransaction`'s header
`source_location`/`destination_location`, which are set only for receipt, transfer, and return — assignment,
delivery, reservation, mark damaged/lost, disposal, and correction/reversal transactions never set them, only their
*lines* carry location data. That meant any authenticated user, regardless of scope, could already view any
assignment/delivery/reservation/disposal transaction's detail page. Fixed with a new shared
`apps.inventory.access.require_transaction_access()` that checks header locations first and falls back to every
line's `from_location`/`to_location`; `TransactionDetailView` and every new document/attachment view use it. Covered
by a dedicated regression test file (`tests/test_inventory_transaction_access.py`) proving the leak is closed for
assignment, reservation, and disposition transactions specifically, plus the original receipt/transfer/return cases
that already worked.

**WeasyPrint on Windows**: needs the GTK3 native runtime (Pango/Cairo/GObject) — Docker's image installs this via
`apt` (`deploy/Dockerfile` updated), but a non-Docker Windows dev machine needs it installed separately
(`winget install --id tschoonj.GTKForWindows -e`), documented in `CLAUDE.md`/`AGENTS.md`. Installed and verified
locally (with the user's approval) so PDF rendering could be tested for real rather than assumed to work once
deployed — every test in this phase, including actual `%PDF`-byte-signature assertions, ran against a real
WeasyPrint render, both via pytest and a live HTTP download through the dev server.

**Acceptance**: acceptance criterion §21.13 — a product rename after document generation changes neither the
stored `context_snapshot` nor the PDF file bytes (both asserted directly, and confirmed live via shell before the
test suite was written). Sequential document numbering, regeneration linking via `supersedes` without touching the
original, and scope-denied download (documents and attachments) are all covered by tests. 249 tests pass (up from
207).

### Prompt 6 — Excel/CSV import — done

Depends on Prompt 3 (products/assets) and Prompt 2 (locations); built after Prompt 7 rather than before it, once
the user supplied a real sample of the legacy workbook locally (`sample/sample.xlsx`, gitignored — it contains
real-looking customer names, project references, and serial numbers, so at the user's choice it and any copy of it
were kept out of git history entirely) — confirming doc 07's column mapping table matches the real file exactly
(`BRAND`, `MODEL/Part No./SKU`, `TYPE/DESCRIPTION`, `S/N`, `QTY`, `LOCATION`, `2nd floor Location`,
`Project Ref. #`, `FINAL CUSTOMER`, `COMMENTS/#No`, `PRODUCT DELIVERY / PRODUCT REMOVAL`, `Arrival Date`,
`Delivery Date`, `Return Date`, `Removal Date`, `Registrar`) down to real quirks like a trailing space in the
`S/N` header and in some `BRAND`/`TYPE` values. The pytest suite exercises this same layout via a synthetic
in-memory workbook built by `tests/imports_fixture_builder.py` (fabricated brand/customer names, same structural
quirks) rather than depending on the real file, so the tests run the same way in any clone.

Delivered the `apps.imports` app: `ImportBatch`/`ImportRow` models exactly per doc 02; `parsing.py` (openpyxl for
`.xlsx`, stdlib `csv` for `.csv`, exact case-insensitive header matching against the known column set — never
fuzzy-matched, per spec §13's "do not guess" instruction — missing required columns rejected before any row is
staged); `normalization.py` (whitespace, quantity, and lenient date parsing — day-first formats tried before
month-first, doc 10's "corporate date format" default); `location_resolution.py` (name match on `LOCATION`,
narrowed by `2nd floor Location` against the matched location's children by name or `code`; ambiguous or unmatched
values are left unresolved rather than guessed, exactly as doc 07 specifies); and `services.py` (staging,
row-level location override, skip, batched execution, template/results CSV generation).

**Scope decision made with the user before implementation**: rather than build a preview-time mapping UI for the
legacy `PRODUCT DELIVERY / PRODUCT REMOVAL` column and the Delivery/Return/Removal date columns (doc 10 open
question #9 — the spec explicitly says not to guess what legacy movement values mean), every row is imported as an
initial stock receipt only, using Arrival Date (or today's date if blank/unparseable) as the receipt date. The
legacy delivery/removal value and the three secondary dates are preserved verbatim in the resulting record's
`notes` for traceability, but never converted into a delivery/return/disposal transaction automatically — any
further movement is entered manually afterward through the normal interactive UI. This is the safer, spec-aligned
default for a first version; a mapping UI can be layered on later without a schema change if needed.

**Scope simplifications, deliberate:**
- The whole `MODEL/Part No./SKU` column value maps to `Product.model`; `Product.sku` is left blank (doc 07 flags
  this column as "ambiguous by nature — never auto-split without a user decision," and the real sample file never
  contains an obvious Model/SKU delimiter to split on). A SKU can be added afterward via the normal product-edit
  screen.
- `TYPE/DESCRIPTION` maps to `Product.product_type` only; `Product.description` is left blank, since the real
  sample's values are short category words ("Firewall", "Router"), not combined type-plus-description text.
- A row with no serial number is quantity-tracked and needs a positive `QTY`; a row with a serial number is
  unit-tracked (one `UnitAsset`), regardless of what `QTY` says — a legacy row with a serial and `QTY=0` (seen in
  the real sample) still yields exactly one unit, since a physical serialized item existing is what the serial
  number itself asserts.
- A brand/model that already exists with the *opposite* tracking method than a row implies is a `failed` row, not
  an auto-resolved one — there's no correction path for this in v1 (no tracking-method-migration UI exists yet
  either, per doc 10 open item #8), so the source file must be corrected and re-uploaded.
- `failed` rows are not retried within the same batch on a later `execute_batch()` call — only `pending`/`warning`
  rows are (see the updated doc 07 Idempotency section for why: a `failed` row's problem is in its own data, and
  v1 has no in-place row-data correction UI, only a `warning` row's location override).
- Every row that reaches execution calls a `_get_or_create_import_product()` wrapper around
  `apps.catalog.services.create_product()`, rather than that function directly — `create_product()` always inserts
  a *new* Product row even with `duplicate_acknowledged=True` (the correct behavior for its interactive "I know
  this looks like a duplicate but I mean it" caller), which would have created a separate duplicate product for
  every row sharing the same brand/model in a batch. Caught and fixed before writing the pytest suite, during the
  live-shell smoke test against real PostgreSQL with the real sample file (4 "Check Point V80W" rows correctly
  resolved to one `Product`, not four).
- Import is Administrator-only (not Administrator + Stock Manager, unlike most inventory writes) — this is a bulk,
  batch-mutating tool intended for the initial data migration and occasional bulk top-ups, not routine movement
  work.

An Administrator can also download a blank template CSV (`imports:template_download`, linked from both the batch
list and upload screens) matching the exact expected column layout with two example rows (one unit-tracked, one
quantity-tracked), for building future mass-import files from scratch rather than only from an export of the old
system.

**Acceptance**: acceptance criterion §21.14 — verified by a live end-to-end run against real PostgreSQL: staging
the real sample file, confirming the two `LOCATION="Customer"` rows correctly warn as unresolved (no such storage
location exists) while the six `Basement 1` rows resolve down to the correct rack via `2nd floor Location`,
executing, then **re-running `execute_batch()` on the same already-completed batch and confirming zero new
`InventoryTransaction` rows were created** — the core idempotent-retry guarantee. Also verified live over real
HTTP (upload → preview → per-row location override → execute → re-execute after override → results CSV download),
which caught a real bug ruled out by neither the shell smoke test nor a first pytest pass: the location-override
and skip views only permitted edits while `ImportBatch.status == "previewed"`, but the first `execute_batch()` call
moves a batch with any remaining warnings to `"partially_completed"` — silently blocking exactly the retry
workflow the feature exists for. Fixed by allowing edits in both `previewed` and `partially_completed` states, and
covered by a dedicated regression test (`test_override_allowed_after_partial_execution`). 342 tests pass (up from
294).

### Prompt 7 — Dashboard, search, reports, disposal reporting — done

Depends on Prompt 4 (movements exist to report on) and Prompt 5 (documents referenced from transaction/report
views). Ran ahead of Prompt 6 (Excel/CSV import): the prompt pack notes import is best sequenced once a sanitized
copy of the real legacy workbook is available, which hadn't been provided yet at this point — it was supplied
afterward, and Prompt 6 was completed next (see below).

Delivered `apps/inventory/filters.py` (`filter_unit_assets()`/`filter_stock_balances()` covering every filter in
spec §14: free-text `q`, brand, model, SKU, type, serial, status, project reference, final customer, supplier,
invoice number, arrival/removal date ranges, location — using the existing `ltree` descendant-or-self lookup so a
location filter includes everything under it — and a `duplicate_serial` flag reusing `services/duplicates.py`'s
logic); `apps.core.csv_export.CSVExportMixin`, a small `ListView` mixin adding `?format=csv` to any list view without
duplicating pagination/scoping logic; a new `TransactionListView` (`apps.inventory.access.scope_transaction_queryset()`
extends the Phase 5 header-or-line scope check to a queryset filter, reusable to also give `AssetStatusHistory` scoped
access); the whole `apps.reporting` app — 11 report queries plus a low-stock-balances query, all scope-aware from the
start (every query takes `user` and filters through the same location-scoping layer as everything else, not just the
list views) — covering every report in spec §15 (current stock, stock by location, reserved stock, employee
assignments, customer deliveries, stock by project reference, temporary assignments, damaged/lost/disposed assets,
movement history, low stock) plus a disposed-items report with a type filter aimed at reviewing disposed HDDs (spec
§9); a new `apps.audit` list view (Administrator-only, event-type/actor/object-type/date filters) exposing the audit
trail that previously had no UI; and `seed_bulk_inventory`, a `bulk_create`-based, DEBUG-gated, idempotent management
command that seeds 8,000+ unit assets for realistic pagination/perf testing (deliberately bypassing the service layer
— running 8,000 rows through `receive_stock()` would be far slower than the data is worth for a synthetic perf
fixture, and no ledger/audit trail is needed for it).

**One real bug found and fixed while building `stock_by_location`**: the initial aggregation used
`.values_list(...).annotate(count=Sum(1))`, a non-idiomatic pattern that doesn't reliably group the way `.values(...)
.annotate(count=Count("id"))` does. Caught before it reached tests; corrected to the standard `.values().annotate()
.values_list()` shape for both the per-location unit count and the per-location balance-quantity sum.

**Acceptance**: acceptance criteria §21.10 (every spec §14 filter, scoped) and §21.15 (responsive at 8,000+ records —
`tests/test_performance.py` asserts bounded query counts via `django_assert_max_num_queries` and correct pagination
against the 8,200-row bulk-seeded fixture) — both verified by tests and a live end-to-end run against real
PostgreSQL. 294 tests pass (up from 249).

## Phase 4 — Migration and hardening

### Prompt 8 — Security, performance, backup, production deployment — done

Depends on everything above existing to harden. Delivered:

- **Login throttling**: `django-axes`, locking by `(username, ip_address)` — see doc 08's Security section for why.
  Verified live against a real login flow (5 failures locks the account, a 6th attempt with the *correct* password
  is still rejected with 429, a *different* account can still log in from the same session/IP) before writing the
  regression tests in `tests/test_login_throttling.py`.
- **Custom error pages**: `templates/403.html`/`404.html` (extend `base.html` — Django's default 403/404 views
  render with a full `RequestContext`) and `templates/500.html` (deliberately *not* extending `base.html` — Django's
  `server_error()` view renders with no context processors at all, by design, in case one of them is what's
  broken, so `500.html` has no `{% if user... %}` or similar that would silently fail). Verified with `DEBUG=False`
  that no traceback or file path ever appears in any of the three (`tests/test_error_pages.py`, including calling
  `django.views.defaults.server_error()` directly for the 500 case).
- **DB-level defense in depth**: `deploy/sql/hardening_runtime_role.sql` plus `RUNTIME_DB_USER`/`RUNTIME_DB_PASSWORD`
  support in `config/settings/production.py` — see doc 08 for the design and the live-Postgres verification
  (`UPDATE`/`DELETE` on every append-only table rejected for the runtime role; ordinary tables and `INSERT`/`SELECT`
  on the append-only ones unaffected).
- **Backup and restore**: `deploy/backup.sh` (`pg_dump` custom format + a `media/` tarball, with retention pruning)
  and `deploy/RESTORE.md`. **Actually executed** against real data: backed up the local dev database, `pg_restore`d
  the dump into a fresh disposable database, ran `manage.py migrate --check` against it (no pending migrations),
  and ran a smoke-test query confirming every row count matched the source exactly — recorded with the date in
  `RESTORE.md`'s "Verified" section, per spec §17's explicit requirement that this be a real, executed gate.
- **Production deployment**: `deploy/Dockerfile.prod` (runtime-only dependencies, `collectstatic` baked in at build
  time, runs as the non-root `app` user, `gunicorn` instead of `runserver`), `deploy/docker-compose.prod.yml`
  (no live code bind-mount, an nginx TLS-terminating reverse proxy in front, healthchecked), `deploy/nginx.conf.example`,
  `.env.production.example`, and `deploy/DEPLOYMENT.md` tying first-time setup, routine deploys, the backup
  schedule, and the hardening role into one runbook.
- **Measured query timings against the 8,000+-row bulk-seeded dataset** (not just query counts): real wall-clock
  timings taken over HTTP against a `runserver` instance running under the actual production settings module
  (`DEBUG=False`, full security middleware stack), logged in as an Administrator. Every paginated list/report view
  came back in roughly 60–150ms. Full CSV exports of the entire 8,000-row filtered set took roughly 1.0–1.2s,
  checked for and cleared of any N+1 query cause (`select_related` already covers every FK rendered per row) —
  reasonable for "generate and stream the whole matching dataset," a fundamentally different operation from a
  paginated list page, so not held to the same target.

**One real bug found and fixed by measuring rather than assuming**: `CurrentStockView`'s "units in stock" table
had no pagination at all — every in-stock `UnitAsset` was rendered in a single response. Invisible in Phase 7's
query-*count* tests (they only exercised the `ListView`-based screens), but obvious once real timing was measured
against 8,000 seeded units: roughly 1.2–1.3s per request, a real violation of acceptance criterion §21.15 ("all
lists are paginated and remain responsive with at least 8,000 imported records"). Fixed with the same manual
`Paginator` pattern already used elsewhere (`ImportBatchDetailView`); timing dropped to ~100ms after the fix.
While fixing it, the same unpaginated-`View` pattern was found and fixed the same way in `ReservedStockView` and
`StockByProjectReferenceView`'s per-reference unit listing (lower risk at today's data shape — nothing seeds
thousands of reserved/project-tagged units yet — but the same spec requirement applies regardless of today's data,
and the fix is the same few lines), plus a missing `.order_by()` on the project-reference query that Django's own
`UnorderedObjectListWarning` flagged once that queryset became paginated (pagination without a stable order can
shuffle rows between pages). Covered by new tests in `tests/test_reporting.py` (`TestCurrentStockReport`,
`TestReservedStockReport`) seeding 55 rows to force a second page.

**Scope notes:**
- Session cookie age (8h idle default) and the password policy (12-char minimum + Django's standard validators)
  were already in place from Phase 1 — Prompt 8 didn't need to change either, only confirm them against doc 08.
- `SECURE_HSTS_PRELOAD` defaults to `False` and is now env-configurable — submitting to a browser's HSTS preload
  list is effectively one-way (removal takes months to propagate), so it's an explicit operator opt-in, not a
  framework default.
- Docker itself was **not** available on the machine this was built on, so `docker-compose.prod.yml`/
  `Dockerfile.prod` follow standard, well-established patterns but haven't been booted end to end via Docker in
  this repository — see the verification note at the top of `deploy/DEPLOYMENT.md` for exactly what *was* verified
  without Docker (the production Django settings module itself, `collectstatic`, a full request/response cycle
  under `DEBUG=False` via `manage.py runserver`, the backup/restore cycle, and the DB role hardening — all against
  real PostgreSQL). Smoke-testing the actual Docker Compose topology once is called out as a pre-cutover step.
- Browser-level (Playwright/Selenium) tests from doc 08's original Testing plan were not built — see doc 08's
  Testing section for why, and it's flagged for Prompt 9's traceability matrix rather than silently dropped.

**Acceptance**: restore procedure actually executed once in a disposable environment (see `RESTORE.md`); measured
(not assumed) query timings reported against 8,000+ seeded records (see above) — both satisfied for real, not just
documented. 353 tests pass (up from 342: axes lockout, error pages, and the reporting-pagination regression tests).
No migration drift.

### Additional feature — scheduled Excel export to a configurable path — done

Not one of the prompt pack's original nine prompts — added directly on the user's request between Prompt 8 and
Prompt 9: an Administrator-configurable local/network path that a full Excel snapshot of unit assets and stock
balances gets written to on a nightly or weekly schedule, as a human-readable safety net alongside the database-
level `pg_dump` backup (`deploy/backup.sh`) built in Prompt 8. The user framed it as "in case of any failure" —
i.e. something a non-technical person could open directly, not a `pg_restore` step.

Delivered as a new small app, `apps.exports`, mirroring `apps.imports`' shape: an `ExportSettings` singleton (doc
02), `services.py` (`build_inventory_workbook()`, `run_export()`, `should_run_today()`), an Administrator-only
settings screen (path + schedule, plus a "Run export now" manual trigger for testing the configured path without
waiting for cron), and a `run_scheduled_export` management command — the same "cron invokes it daily, the command
decides internally whether today is a run day" pattern as `deploy/backup.sh`, documented in `deploy/DEPLOYMENT.md`.

The workbook has two sheets, `Unit Assets` and `Stock Balances`, using the same column sets as the existing CSV
exports (`UnitAssetListView`/`StockBalanceListView`) — unscoped and unfiltered (every asset regardless of status),
since this is a system-level backup an Administrator configured, not a user-facing scoped report.

**Verified live** (service layer via shell, then the full HTTP flow — save settings, run now, confirm a real
324KB two-sheet workbook was written and is readable, confirm the management command's no-op/run paths, confirm a
disconnected/unreachable path is caught and recorded as a failure rather than crashing or silently doing nothing)
before writing the pytest suite (`tests/test_exports_services.py`, `tests/test_exports_views.py`,
`tests/test_exports_management_command.py` — 26 tests). A genuinely unreachable path is rejected immediately at
save time (a real filesystem write-test, not just a format check) so a typo isn't discovered for the first time at
2am by a failed cron job.

**Filter coverage confirmation** (also raised by the user in the same request): the asset list already filters by
project reference, final customer, serial number, supplier, and type — all delivered in Prompt 7
(`apps/inventory/filters.py`, `templates/inventory/asset_list.html`). No gap found; no change needed.

**Acceptance**: a real 8,000+-row-scale export was written and read back successfully during verification (the
same bulk-seeded dataset from Prompt 8's timing measurements). 379 tests pass (up from 353). No migration drift.

### Additional infrastructure — GitHub Actions CI and repository publish — done

Also on the user's request: published the repository to GitHub
([`github.com/prodromou27/stock-inventory`](https://github.com/prodromou27/stock-inventory), private — kept
private rather than public since this is an internal company tool, even though no real secrets are committed
anywhere in history; confirmed with a full history scan before pushing) and added `.github/workflows/ci.yml`, a
GitHub Actions workflow running ruff, `black --check`, the migration-drift check, and the full pytest suite against
a real Postgres 16 service container on every push — automating the manual verification cycle used by hand every
phase up to this point.

**One real, genuinely valuable bug caught by the first real CI run**: the exports test suite's "unreachable path"
fixtures used a hardcoded Windows drive-letter string (`"Z:\\...\\..."`) to force a real filesystem failure. That
only fails on Windows — GitHub Actions' Linux runners don't treat backslashes as path separators, so the string was
just an unusual-but-valid relative path component, and `os.makedirs()` happily created it there instead of raising.
All local testing this whole session ran on Windows, so this was invisible until the first genuinely cross-platform
run. Fixed with a real cross-platform fixture (`tests/conftest.py`'s `unwritable_path`): a regular file placed
where a directory component needs to be, so walking into it as a directory reliably raises `OSError` on any OS.
This is exactly the kind of gap CI is supposed to catch before it reaches anyone else's machine — first real CI run
found it within minutes.

### Prompt 9 — Final acceptance and release audit

Depends on all prior prompts. Delivers: traceability matrix (every §21 criterion and §22 out-of-scope item mapped
to code/tests), full test/lint/security run, Critical/High findings fixed with regression tests, administrator and
stock-manager quick-start docs, release recommendation.

**Acceptance**: the traceability matrix itself, with no unmapped §21 criterion.

## Sequencing notes

- Prompt 0 (this package) has no code dependency and is complete.
- Prompts 1→2→3→4 are strictly sequential (each app depends on the previous one's models).
- Prompt 5 and Prompt 6 both depend on Prompt 3/4 but not on each other — could be parallelized across two sessions
  if desired, at the cost of the cross-tool review step being more valuable at the merge point.
- Prompt 7 depends on the outputs of 4, 5, and 6 (reports read transactions, documents, and — for import-sourced
  data — imported rows) so it is last among the "build" prompts. In practice Prompt 7 was built *before* Prompt 6,
  since the real legacy workbook Prompt 6 needed wasn't supplied until afterward — harmless in practice, since
  import-sourced rows become ordinary `InventoryTransaction`/`UnitAsset`/`StockBalance` rows once executed, which
  Prompt 7's reports already cover with no import-specific logic of their own.
- Prompts 8 and 9 are hardening/audit passes over the whole system and must come last.
