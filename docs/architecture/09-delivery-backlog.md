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

### Prompt 9 — Final acceptance and release audit — done

Delivered [`11-traceability-matrix.md`](11-traceability-matrix.md): every spec §21 criterion (1–15) mapped to the
specific service/view function and the specific passing test function(s) that verify it — not paraphrased, every
reference spot-checked to confirm the named function actually exists before being written down. Every spec §22
out-of-scope item confirmed genuinely absent by direct code search, not merely "never mentioned." A "Known gaps"
section records what's honestly incomplete (no browser-level UI tests, the production Docker Compose topology not
yet booted end to end, no in-app user-creation screen) rather than letting the matrix imply more coverage than
exists.

**Two real findings from the audit, both fixed with regression tests**:
- **Acceptance criterion §21.8** ("condition and accessories recorded at issue and return") had test coverage on
  the issue side only — the return side (`returns.py::return_stock`) already threaded `condition`/`accessories`
  through to the line snapshot correctly, it just had no dedicated test proving it. Added
  `tests/test_inventory_returns.py::test_condition_and_accessories_captured_on_return`.
- **No in-app way to reach Django's `/admin/` site** (which is how a new user account gets created and assigned a
  role at all — spec §14's "User and permission administration" screen; `apps.accounts`' own screens only cover
  location-scoped access grants) for any Administrator except the original `createsuperuser` account, since
  nothing kept `is_staff` in sync with the app's own Administrator group. Fixed with a signal
  (`apps/accounts/signals.py::sync_is_staff_with_administrator_group`), verified live against the real dev
  database before writing `tests/test_admin_staff_sync.py`.

A broader security sweep (SQL injection, XSS/autoescaping, unsafe `eval`/`pickle`/`subprocess`, secrets in git
history, `manage.py check --deploy`, security headers) found nothing else — see the matrix's "Security review"
section for the full list of what was checked and how.

Delivered `docs/administrator-quickstart.md` and `docs/stock-manager-quickstart.md` — task-oriented guides for the
two roles that actually use the app day to day (distinct from the architecture docs, which are for developers).
Also refreshed `docs/architecture/README.md` (its status line and "recommended next step" had gone stale — it
still said "no application code has been written yet" and pointed at running Prompt 1) and
`docs/architecture/01-repository-structure.md` (missing `apps.exports`, and several paths that had drifted from
what was actually built — `scripts/` never existed, backup/restore/deployment docs live directly under `deploy/`).

**Acceptance**: the traceability matrix itself, with no unmapped §21 criterion — delivered, and every one of its
test references verified to actually exist. 384 tests pass (up from 383, from the one new regression test — the
`is_staff` sync fix's own tests were already counted in Prompt 8's "additional infrastructure" entry above). No
migration drift.

### Additional feature — editable document (sign-off/delivery) PDF templates — done

Added directly on user request, after Prompt 9's release audit. The user asked, in one request: (1) for
Administrators to be able to preview and edit the printable report/document templates "from settings" — especially
the sign-off/delivery form — including a logo and mapping the same dynamic data fields the current form already
uses; and (2) for the "product delivery acceptance and sign-in form" generated when stock is handed over to a
customer to also use an editable template. Clarified with the user before building: the trigger is the existing
Delivery movement/document (not a new document type — Delivery already generates exactly this sign-off form), the
editor should be an HTML/code editor using Django's existing `{{ field }}` syntax (not a visual drag-and-drop
builder — a materially larger, separate subsystem), and only the printable Assignment/Delivery PDF template(s) need
to become editable, not every report screen.

See doc 02's `DocumentTemplate` entry and doc 06's "Editable document templates" section for the full design and
the explicit trust-model reasoning for letting an Administrator-authored template render through Django's template
engine. In short: `apps/documents/pdf.py::render_pdf()` now checks for an Administrator-saved `DocumentTemplate`
override per `DocumentType` before falling back to the packaged `form_v1.html` file — purely additive, nothing
changes for an installation that never touches it. A new **Document Templates** screen (Administrator-only) offers
a code editor pre-filled with the packaged template as a starting point, a documented list of every available field
(exactly `build_document_context()`'s output, i.e. nothing new to learn beyond what the packaged template already
demonstrates), a logo upload embedded as a base64 data URI at render time, a **Preview** button that renders the
in-progress (not-yet-saved) template against realistic sample data and opens a real PDF in a new tab, and **Reset
to packaged default**.

**One real bug found by my own tests, fixed**: the Preview endpoint only caught `ValidationError`, but a template
with a genuine syntax error raises `django.template.exceptions.TemplateSyntaxError` directly from the render call
— `apps/documents/template_services.py::update_template()`'s save-time validation already wrapped this correctly,
but `render_preview_pdf()` didn't, so previewing a broken template crashed with a raw 500 instead of the clean 400
the UI expects. Caught by `tests/test_document_templates_views.py::TestPreviewView::test_broken_template_returns_400_not_500`
before this was ever exercised by a real user; fixed by wrapping `render_preview_pdf()`'s render call the same way.

Verified live end-to-end (service layer, then full HTTP: save a custom template with a logo, preview it as a real
PDF, confirm a broken template is rejected without touching the previously-saved good one, generate a real document
against the override, reset to default) before writing the pytest suite (`tests/test_document_templates_services.py`,
`tests/test_document_templates_views.py` — 33 tests).

**Acceptance**: an Administrator can edit, preview, and reset the Delivery (and Assignment) document template from
the app itself, with a logo and the documented field set, verified against a real rendered PDF at every step.
417 tests pass (up from 384). No migration drift.

### Additional feature — UI redesign (sidebar shell, design system) — done

Added directly on user request ("The app needs much improvement... UI"). The original UI was a single ~100-line
stylesheet, browser-default form rendering, plain `<table>` markup, and a narrow centered column not suited to a
desktop inventory tool. `static/css/app.css` was rebuilt as a full design system (color/spacing/radius/shadow
tokens with light+dark mode, a sidebar/topbar app shell, buttons, form controls, tables, badges, cards, toolbars,
pagination) with strong global defaults so untouched markup still renders consistently. `templates/base.html`
became a fixed sidebar with grouped, active-highlighted navigation and a full-width content area; unauthenticated
pages (login, forced password change) get a distinct centered auth-card instead of the app shell.
`apps/core/templatetags/ui_extras.py` added `badge_class` (maps status values to color-coded badges) and
`nav_active`/`nav_active_app` (sidebar active-link highlighting). 47 templates were updated for consistency: page
headers with title + action buttons, status badges, definition-list/card detail layouts, the dashboard and three
hub pages (Movements, Reports, Document Templates) turned into action-card grids, filter forms restyled as
toolbars.

### Additional feature — Settings hub, system configuration, certificate upload, sortable grid — done

Added directly on user request, in the same conversation as the UI redesign above: "much improvement... on UI,
functionality, features... Settings tab required... build any features that may be useful." Clarified with the
user before building (the request was too open-ended to act on blindly per this doc's own "stop and ask rather
than inventing a new rule" — spec §23.10): consolidate the existing scattered admin screens under one Settings
entry point, add system configuration (site branding, an `ALLOWED_HOSTS` portal override) and TLS certificate
upload (both previously flagged as gaps in `deploy/DEPLOYMENT.md`), and — for "the inventory grid must be useful"
with many items — sortable columns and a real fix for pagination silently dropping active filters.

**New `apps.settings` app** (Django app label `sysconfig` — distinct from `config.settings`, the Django settings
*module* package, to avoid reader confusion): a `SystemSettings` singleton (`site_name`, `logo`, comma-separated
`allowed_hosts_override`) mirroring `ExportSettings`'s `.load()` singleton pattern, but with one deliberate
difference — `.load()` never writes: `get_or_create()`'s SELECT+INSERT/savepoint queries on a cache miss aren't
acceptable on a lookup that now runs on *every* request (see below), so `.load()` is a plain `SELECT ... LIMIT 1`
that returns an unsaved `pk=1` instance until an Administrator actually saves something. `apps/settings/services.py`
follows the established `require_role` + `full_clean()` + `record_event()` pattern for both settings updates and
certificate uploads (the latter validated structurally — cert/key are a matching pair — via the stdlib `ssl`
module, not a claim about CA trust or expiry). A new **Settings** sidebar entry (replacing separate Manage
Access / Export Settings / Document Templates links) opens a hub linking all administrator screens, including the
two new ones.

**Branding and the `ALLOWED_HOSTS` override reach every request**, not just the Settings screens themselves —
`apps.settings.middleware.SystemSettingsMiddleware` (first in `MIDDLEWARE`, before `SecurityMiddleware`, because
`SECURE_SSL_REDIRECT=True` makes `SecurityMiddleware` validate the host on every production request) applies the
override to `django.conf.settings.ALLOWED_HOSTS` — a blank override reverts to the env-configured default captured
once at process start, so this is purely additive over the existing wildcard-by-default behavior (doc 04's
"Default admin bootstrap" section and this doc's Prompt 8 entry). **Recovery if a bad override locks an
Administrator out**: connect to the server and clear the row directly — `python manage.py shell -c "from
apps.settings.models import SystemSettings; s = SystemSettings.load(); s.allowed_hosts_override = ''; s.save()"` —
the same pattern as the existing axes-lockout recovery command in `CLAUDE.md`. `apps.settings.context_processors.
branding_context` (site name + logo, used by `base.html`'s sidebar/auth-card brand) reuses the middleware's lookup
via a request attribute instead of querying `SystemSettings` a second time — `tests/test_performance.py`'s asset-
list query-count budget went from 10 to 11 to account for this one new, fixed-cost (not row-count-scaling) query.

**TLS certificate upload** writes to `settings.CERTS_DIR` (new: `config/settings/base.py`, defaults to
`deploy/certs`, matching install.sh's existing self-signed-cert location; test settings point it outside the repo
tree). `deploy/docker-compose.prod.yml`'s `web` service now also bind-mounts `./certs` (read-write; `proxy`'s
existing mount of the same host directory stays read-only), and `deploy/install.sh` `chmod 777`s that directory so
`web`'s non-root `app` user can write to it regardless of host UID/GID. Deliberately does **not** reload nginx
automatically — doing so would need `web` to control the Docker daemon (a `docker.sock` mount), a security
trade-off not worth making for this; the Settings screen and `deploy/DEPLOYMENT.md` both say so plainly, and the
operator still runs `docker compose -f deploy/docker-compose.prod.yml restart proxy` once, same as today's
manual-file-drop process minus the "get the file onto the server" step.

**Inventory grid**: `apps.inventory.views.UnitAssetListView` gained an explicit `SORT_FIELDS` allow-list (product,
serial, status, location, arrival date) and `?sort=`/`?dir=` query params; `templates/_sort_th.html` +
`{% sort_th %}` (new inclusion tag in `ui_extras.py`) render each clickable `<th>`, reusable for future sortable
grids. Separately — and found while building this, not something the user explicitly named — **every paginated
list's Next/Previous links silently dropped active filters** (`?page=2` without carrying `?status=...&q=...` along
with it); fixed across all 20 affected templates using Django 5.1's built-in `{% querystring %}` tag, which
preserves the full current query string and only overrides the key(s) given.

Verified live end-to-end (service layer, then full HTTP: settings save reflected in the sidebar immediately,
`ALLOWED_HOSTS` override applied/reverted via the middleware directly, a real self-signed cert/key pair
saved and a mismatched pair rejected, sort links toggle direction and carry active filters) before/alongside the
pytest suite (`tests/test_settings_models.py`, `tests/test_settings_services.py`, `tests/test_settings_views.py`,
`tests/test_settings_middleware.py`, plus `TestUnitAssetListSort` in `tests/test_inventory_views.py` — 39 new
tests). 481 pass locally (12 pre-existing, environment-only `tmp_path` failures on this Windows dev machine
unrelated to this change — see `tests/conftest.py`'s `certs_dir`/`unwritable_path` fixtures' docstrings; CI is
unaffected). No migration drift beyond the one new `sysconfig.SystemSettings` table.

**Acceptance**: Administrators reach every configuration screen from one Settings entry; can rebrand the sidebar
(name + logo) and tighten `ALLOWED_HOSTS` from the browser; can upload a real TLS certificate without needing
shell/SCP access to the server; can sort the Assets grid by any of five columns; and no longer lose their filters
by paging through a large result set.

### Additional feature — Quick Receive (batch serials), Tailwind CSS build pipeline — done

Added directly on user request, in the same conversation as the two entries above. Two unrelated asks handled
together: "the current asset list may be in a grid so it can be easiest and faster to add new records" (clarified
to mean: batch-entering many serials at once, not a spreadsheet-style inline-editable grid — the latter would mean
arbitrary live-editing of every page's core business data with no service-layer validation boundary, a much larger
and riskier feature than what was actually being asked for), and, separately mid-turn, "we may consider using
Tailwind CSS?" — clarified to a real build pipeline (not the CDN script, which Tailwind's own docs say not to use
in production) after which the user confirmed **Full Tailwind with a real build pipeline**.

**Quick Receive** (`apps.inventory.views.QuickReceiveView`, linked from the Assets grid's page header and the
Movements hub): one product/location/date, a textarea of serials (one per line), submitted together.
`apps.inventory.services.receipts.receive_stock_batch()` — new, alongside the existing `receive_stock()` it calls
once per line — deliberately does **not** write one `InventoryTransaction` with many lines; each physical unit's
arrival stays its own receipt event, consistent with how a single manual receive already works, and a per-serial
result list (created/duplicate/error) means one bad row doesn't cost the rest of the batch, unlike a single atomic
all-or-nothing transaction would. Never auto-acknowledges a duplicate serial — that confirmation is a deliberate
human decision (doc 05) — a duplicate row is reported back for the operator to resolve individually, not silently
accepted. Permission/product-state checks run once up front, not per row.

**One real bug caught by my own test, fixed**: `QuickReceiveForm.clean_vendor_serials()`'s "enter at least one
serial" check was unreachable dead code — Django's `CharField` defaults to `strip=True`, so a whitespace-only
submission already fails the field's own `required` check before any custom `clean_<field>()` method runs, and
if the required check *passes* (non-blank after stripping only the string's outer edges), there necessarily was a
non-blank line somewhere for my code to find. Fixed by making the field `required=False` so the friendlier custom
message actually fires for the empty case.

**Tailwind CSS build pipeline**: `static/css/app.css` is no longer hand-written or committed — it's a generated
artifact of `assets/tailwind/input.css`, compiled by `npm run build:css` (`package.json`/`tailwind.config.js`,
new). The existing hand-rolled design system's CSS is preserved **exactly** (every rule moved into Tailwind's
`@layer base`/`@layer components`, values unchanged) rather than rewritten as utility-first classes across all 55
templates — the templates' existing class names (`.btn`, `.card`, `.badge`, `.sidebar__link`, …) are untouched, so
this is a genuine, working Tailwind build pipeline (Node, content-scanning, minification) with zero visual-
regression risk and zero template churn, not a cosmetic rename. `tailwind.config.js`'s `theme.extend` maps
Tailwind's utility scale (`bg-primary`, `rounded-md`, `shadow-sm`, …) onto the *same* CSS custom properties the
design system already used for light/dark theming, so any future template that reaches for a Tailwind utility
class directly gets automatic dark-mode support for free, with no `dark:` variants needed anywhere.

Docker's dev and prod paths need different build strategies because their volume strategies already differ (doc
09's earlier entries / `deploy/Dockerfile.prod`'s own comment): prod's `Dockerfile.prod` never bind-mounts source,
so a `node:20-slim` build stage compiles the CSS once and only the compiled file crosses into the runtime image —
Node itself never ships. Dev's `docker-compose.yml` bind-mounts the whole repo into `web` (`..:/app`), which would
shadow anything compiled at `web`'s own image-build time, so a separate `assets` sidecar container runs
`npm run watch:css` continuously against that same bind mount instead. Outside Docker, `npm install && npm run
build:css` (or `watch:css`) is now a required one-time step — documented in `CLAUDE.md`'s new "Frontend build"
section — since the page renders unstyled, not broken, until it's run at least once. CI (`.github/workflows/ci.yml`)
runs the real build before pytest, since templates reference the compiled file directly.

Verified live end-to-end (`npm install && npm run build:css` producing the exact expected rules/values, Django
serving the compiled file and every spot-checked page rendering unchanged, Quick Receive's per-row outcomes for a
mixed created/duplicate batch) before/alongside the pytest suite (`TestReceiveStockBatch` in
`tests/test_inventory_receipts.py`, `TestQuickReceiveView` in `tests/test_inventory_views.py` — 15 new tests). 482
tests pass locally (same 12 pre-existing, environment-only `tmp_path` failures noted in the entry above; CI
unaffected). No migration drift.

**Acceptance**: a Stock Manager can receive a whole box of serialized units in one submission instead of one
per page load, with a clear per-serial outcome if something in the batch needs attention; the app's visual design
is unchanged, now compiled by a real, verified Tailwind build pipeline instead of a hand-written stylesheet.

### Additional feature — sortable columns everywhere, dashboard KPIs — done

Added directly on user request, continuing the same conversation: "make sure that our product is useful, with
modern UI, all the relevant features that needed exist... must be useful, and not time consuming." Two changes,
both extending patterns already built rather than inventing new ones.

**Sortable columns** (`apps.inventory.views.UnitAssetListView`'s `?sort=`/`?dir=` support, added in the entry
above) is now `apps.core.sorting.SortableListMixin` — pulled out once a fifth view needed the identical ~15 lines,
not before. Applied to Products, Locations, Stock Balances, and Transactions, each with its own explicit
`sort_fields` allow-list (never a raw user-supplied field path) and `default_ordering` tiebreaker matching what
that view already used unsorted. `StockBalance.available_quantity` is a computed `@property` (on_hand minus
reserved), not a database column, so it's deliberately left out of the sortable set rather than adding an
`.annotate()` for it — on-hand and reserved alone cover sorting by either component. `UnitAssetListView` itself
was refactored onto the shared mixin too, so there's exactly one implementation of this logic now, not five.

**Dashboard KPIs**: `apps.core.views.HomeView` no longer just links to other screens — `apps.reporting.queries.
dashboard_summary()` (new, alongside this module's other report queries, following the same
`scope_queryset()`/`scope_transaction_queryset()` pattern every one of them already uses, so the numbers respect
the viewer's location access exactly like every list/report screen already does) returns seven cheap counts/
aggregates — units in stock, quantity on hand, low-stock alerts, active reservations, damaged, lost, and
transactions in the last 7 days — rendered as clickable `.stat-card`s that jump straight to the relevant filtered
list or report. Every value is a `.count()`/aggregate, never a loaded queryset, so this stays cheap regardless of
inventory size — verified live against the `seed_bulk_inventory`-seeded dev database (8,000+ assets) before
writing the pytest suite, not just against small fixture data.

Verified live end-to-end (sorting each of the four newly-wired grids by every one of their sortable columns, the
dashboard's real numbers against the bulk-seeded database) before/alongside the pytest suite (`apps/core/
sorting.py`'s behavior re-verified through each view's own light sort tests rather than duplicated in isolation,
plus `tests/test_dashboard.py` — 8 new tests for `dashboard_summary()` and the view). Full count and pre-existing
`tmp_path` caveat unchanged from the entry above; CI unaffected. No migration drift.

**Acceptance**: every major grid (Assets, Products, Locations, Stock Balances, Transactions) supports click-to-sort
on its key columns; the dashboard shows real, scoped, clickable numbers instead of only navigation links.

### Additional feature — Quick Add Products (bulk product entry) — done

Added directly on user request: "the products are shown in grid like excel? ... make sure that is useful for the
users, simplify it if needed." Asked via `AskUserQuestion` how far to take the "spreadsheet-like" idea — the user
picked bulk quick-add for new products specifically, not a true inline-editable grid on the existing list and not
leaving the current table as-is.

`apps.catalog.services.create_products_batch(*, user, rows)` mirrors `receive_stock_batch()`'s shape exactly: it
calls the existing single-item `create_product()` once per row rather than a bulk insert, so each row gets its own
duplicate check and audit event exactly like creating it individually would, and one bad row (a Brand/Model/SKU
match, a validation failure) doesn't cost the rest of the batch — it returns a per-row outcome list
(`created`/`duplicate`/`error`) instead of raising. A duplicate row is never auto-acknowledged; it's reported back
for the operator to resolve individually, same reasoning as the single-product form
(`docs/architecture/05-tracking-and-duplicates.md`).

`apps.catalog.views.QuickAddProductsView` renders a 10-row `django.forms.formset_factory` grid
(`QuickAddProductFormSet`/`QuickAddProductRowForm` in `apps/catalog/forms.py`) with only the fields needed to
create a product fast (Brand/Model/SKU/Type/Tracking/Supplier) — description, supplier notes, and low-stock
threshold stay follow-up edits on the created product rather than bulk-entry fields. Reachable from the Products
list via a new "+ Quick add (multiple)" button next to "+ New product".

**Bug found and fixed during live verification** (not caught by any test, since none existed yet for this feature):
submitting two filled rows out of ten produced spurious "Required." errors on filled-in fields. Root cause was two
compounding issues:
1. `QuickAddProductRowForm.tracking_method` originally set `initial=TrackingMethod.UNIT` and `clean()` gated
   "is this row blank" on `self.has_changed()` — but a `<select>` always submits a concrete value once rendered,
   so there's no true "untouched" state to compare against `initial`, and `has_changed()` misfired on rows the
   operator never touched. Fixed by removing the field-level `initial` and gating blank-row detection on whether
   the *identifying* fields (brand_name/model/product_type_name) are non-blank instead.
2. `Form.full_clean()` always sets `self.cleaned_data = {}` before running field validation, so even a "blank" row
   ends up with a `cleaned_data` dict of empty strings — truthy, not `{}`. The view's original
   `[row for row in formset.cleaned_data if row]` used dict truthiness and would have kept blank rows. Fixed to
   filter on `row.get("brand_name")` specifically.

Verified live end-to-end via `manage.py shell` + `django.test.Client` after both fixes (multi-row creation
succeeding correctly) before writing the pytest suite, so the tests codify the fixed behavior rather than the bug.
`tests/test_catalog_services.py::TestCreateProductsBatch` (created/duplicate/one-bad-row-does-not-block-the-rest/
role requirement) and `tests/test_catalog_views.py::TestQuickAddProductsView` (anonymous/read-only-forbidden,
multi-row creation, blank rows silently skipped, no-rows-entered error, partial row shows field errors, mixed
created/duplicate outcome) — 11 new tests, all passing. `ruff`/`black`/`makemigrations --check` clean; no migration
needed (no new models).

**Acceptance**: Stock Managers/Administrators can create several products in one submission from the Products
list; blank rows are ignored; a duplicate or invalid row is reported per-row without blocking the rest of the
batch; Brand/Type are auto-created exactly like the single-product form already does.

### Additional feature — no-HTML document template editor (structured branding panel) — done

Added directly on user request: "The report template editor can be something else except of html? it can be edit
panel? ... I want the user to be able to edit it without the knowledge of HTML. Adding logos, space, change the
Fonts, etc. The data of most of the reports will be dynamic mapped." Interpreted (confirmed via that answer) as: the
actual report DATA fields must stay automatically mapped — never hand-typed as template syntax — while branding
(logo, spacing, fonts) becomes a structured, no-code panel.

Replaces the previous raw-HTML-textarea editor (`DocumentTemplateForm`, a `<textarea>` bound to
`DocumentTemplate.html_source`) with `apps.documents.forms.DocumentTemplateStyleForm` — five fields, all branding,
no template syntax anywhere in the UI: logo upload, logo position (left/center/right), an accent color (a real
`<input type="color">`), a font choice, and a page-margin preset. Font choices are restricted to `fonts-liberation`
(`deploy/Dockerfile`'s only installed font package) so "Font" always renders as chosen rather than silently
substituting.

**Design choice — additive, not a rewrite of the rendering pipeline**: `DocumentTemplate.html_source` (what
`apps.documents.pdf.render_pdf()` actually reads) is unchanged in meaning — still the final composed template
string — but an Administrator never types it directly anymore. `apps.documents.pdf.render_styleable_source()` (new)
takes the four structured choices and composes it from a new packaged skeleton
(`templates/documents/pdf/styleable_base.html`, the same data-field layout as the existing `form_v1.html` packaged
default, just parameterized) via plain `str.replace()` on deliberately non-Django-template-syntax tokens
(`__FONT_STACK__`, `__ACCENT_COLOR__`, etc.) — so this substitution can never collide with the `{{ }}`/`{% %}` data
tags the skeleton already contains for `document_number`, `lines`, signatures, and so on. Those stay exactly where
the skeleton puts them, satisfying "dynamic data mapping" by construction rather than by trusting an Administrator
never to touch them.

The four structured choices are also persisted as their own `DocumentTemplate` fields (`logo_position`,
`accent_color`, `font_choice`, `page_margin` — migration `0004_documenttemplate_accent_color_and_more`, all with
model defaults matching the packaged look), so re-opening the editor shows back the Administrator's actual previous
choices rather than an unreadable composed HTML blob. `apps.documents.template_services.update_template()` gained
these four as optional keyword arguments (default `None` — a value is only written when explicitly passed) purely
so it keeps working unchanged for every existing/direct caller (this module's own test suite calls it with only
`html_source=`); the structured editor is simply the one caller that also passes them. `accent_color` is validated
against `^#[0-9a-fA-F]{6}$` before ever reaching the composed HTML's `<style>` block — an unvalidated value there
would be a CSS-injection path into WeasyPrint's renderer.

An untouched document type (no `DocumentTemplate` row ever saved) still renders via the original, unparameterized
`templates/documents/pdf/form_v1.html` exactly as before `render_pdf()`'s fallback path is unchanged — so this is
zero behavior change for every document type nobody has customized yet.

Verified live end-to-end via `manage.py shell` + `django.test.Client` against the dev database (migration applied,
GET confirmed no `<textarea>`/no raw `{{ }}` text on the page, POST saved all four structured fields and composed
`html_source` correctly, Preview rendered a real PDF) before/alongside the pytest suite. New/updated tests:
`tests/test_document_templates_services.py` (`TestRenderStyleableSource` — data fields match the packaged default,
style values are applied, output renders as a real PDF; `TestUpdateTemplateStyleFields`) and
`tests/test_document_templates_views.py`'s `TestEditView`/`TestPreviewView` rewritten for the structured form (a
bad `accent_color` now surfaces as a field error/400, not a broken-template render failure, since the composed
HTML can no longer be malformed by admin input). `ruff`/`black`/`makemigrations --check` clean; full suite
527 passed (pre-existing, unrelated `tmp_path` Windows-permission caveat unchanged from earlier entries — CI
unaffected).

**Acceptance**: an Administrator can rebrand the assignment/delivery PDF (logo, its position, an accent color, a
font, page margins) entirely through form controls — no HTML or template syntax visible anywhere in the editor —
and the document's actual data (document number, transaction details, line items, signatures) is always placed
automatically and can't be broken from this screen.

### Additional feature — Brand/Type autocomplete (Phase 1 of a 5-phase structured-feature wave) — done

Added directly on user request, following a competitive-review prompt ("check similar products like Odoo/InvenTree
... more structured") that was scoped into 5 concrete phases via a planning pass (see
`C:\Users\cprodromou\.claude\plans\silly-sniffing-moler.md` for the full 5-phase plan). This is the first,
lowest-risk phase; the other four (ad-hoc report builder, an indented location tree, admin-defined product custom
fields, a spreadsheet-style product grid) follow as their own entries.

`Brand`/`ProductType` were always normalized DB tables resolved via case-insensitive get-or-create
(`apps.catalog.services.get_or_create_brand`/`get_or_create_product_type`), but every entry point
(`ProductForm`, `QuickAddProductRowForm`) rendered `brand_name`/`product_type_name` as plain free-text inputs with
no indication a predefined list existed. Fixed with a native HTML `<input list>`/`<datalist>` combo — deliberately
not a JS-driven combobox, since this codebase has zero JS/AJAX infrastructure (confirmed during the phase-1
exploration pass) and a native datalist needs none: `apps.catalog.views._catalog_choices()` (new) returns every
active Brand/ProductType name, sorted, added to context on every render path of `ProductCreateView`,
`ProductUpdateView`, and `QuickAddProductsView` (including error/duplicate/no-rows re-renders, not just the
first GET); `product_form.html`/`quick_add_products.html` each gained two `<datalist>` blocks. Typing a name not in
the list is unchanged — still creates a new Brand/ProductType exactly as before; this is purely a discovery aid,
never a hard choice set (`get_or_create_*` still does the case-insensitive resolution it always did).

Verified live end-to-end via `manage.py shell` + `django.test.Client` against the dev database (datalist renders
with active brands, a newly-typed brand both creates the product and appears in the datalist on the next page
load) before/alongside the pytest suite. New tests: `tests/test_catalog_views.py::TestBrandTypeAutocomplete` (sorted
active-only choices on all three entry points, inactive brands excluded, a new name still creates). `ruff`/`black`/
`makemigrations --check` clean — no model changes, no migration. Full suite unaffected (pre-existing, unrelated
`tmp_path` Windows-permission caveat from earlier entries still applies; CI unaffected).

**Acceptance**: typing in the Brand or Type field on any product-entry screen shows existing values as suggestions;
picking one behaves identically to typing it manually; typing something new still creates it, exactly as before.

### Additional feature — location tree view (Phase 2 of the structured-feature wave) — done

Phase 2 of the 5-phase plan (`C:\Users\cprodromou\.claude\plans\silly-sniffing-moler.md`; Phase 1 was Brand/Type
autocomplete, above). The flat, paginated `LocationListView`/`location_list.html` becomes a nested, expandable tree
by default — `<details>/<summary>` per node, no JavaScript.

The tree is built in Python (`apps.locations.views._build_location_tree()`), not via `.order_by("path")`, because
sibling order in the ltree path is each node's UUID-hex label — effectively random — not name; the new function
groups the already-scoped/filtered flat queryset by `parent_id` and sorts every level alphabetically. A "root" is
any location whose parent isn't itself present in the (already-scoped) list — for an Administrator that's exactly
the Country-level rows, but `apps.locations.scoping.scope_queryset()` only ever returns a non-Administrator's
granted node(s) and their descendants, never the ancestors above them, so a Stock Manager granted a Storage Room
gets a tree rooted at that Storage Room directly, not at a Country node they can't see — confirmed by a live check
against the dev database and by `TestLocationListTreeMode::test_scoped_stock_manager_tree_roots_at_their_granted_node`.

The existing `level` filter doesn't compose with a tree (filtering to one level discards the ancestry a tree needs
to nest), so it now falls back to exactly the prior flat, sortable, paginated table — `LocationListView.
get_paginate_by()` returns `None` in tree mode (unpaginated; locations are a bounded, curated hierarchy) and the
existing `paginate_by = 50` only when a level filter is active. `SortableListMixin`/`?sort=`/`?dir=` still work in
that flat fallback; they don't apply to (and are silently ignored by) the default tree view, since there's no
column-header table to sort in tree mode.

Verified live end-to-end via `manage.py shell` + `django.test.Client` against the dev database (tree renders with
`<details>/<summary>`, no pagination nav; a `?level=` filter renders the old flat table instead, no tree markup)
before/alongside the pytest suite. New/updated tests: `tests/test_locations_views.py::TestLocationListTreeMode`
(tree-mode flag, alphabetical root/child ordering, the scoped-root behavior above) and `TestLocationListView`'s
sort test moved onto the `level=`-filtered flat fallback (the only mode where `?sort=` still has an effect).
`ruff`/`black`/`makemigrations --check` clean — no model changes, no migration. Full suite unaffected.

**Acceptance**: the Locations page shows an expandable/collapsible tree by default; a Stock Manager sees a tree
rooted at exactly what they're granted, never ancestors above it; filtering by level still works as the familiar
flat, sortable table it always was.

### Additional feature — Product custom fields (Phase 3 of the structured-feature wave) — done

Phase 3 of the 5-phase plan (`C:\Users\cprodromou\.claude\plans\silly-sniffing-moler.md`; Phases 1–2 above). An
Administrator can add extra fields to the product form (e.g. "Warranty expiry") from Settings, no code deployment
needed — matching Odoo/InvenTree-style custom-field support, scoped down to what this app actually needs.

`apps.catalog.models.ProductCustomFieldDefinition` (new; `name` unique, `field_type` — Text/Number/Date/Yes-No —
`is_active`, `display_order`) is Administrator-managed via a new screen linked from the Settings hub (kept in
`apps.catalog`, not `apps.settings`, matching how Document templates/Scheduled export are already only *linked
from* Settings while owned by their own apps). A definition's `field_type` can never be edited after creation —
this codebase's established answer to "a type change would make existing stored values meaningless" is to lock
rather than allow silent reinterpretation (the same reasoning `Product.tracking_method` already uses); deactivating
a definition removes it from the form going forward without discarding values already stored under it.

Values live in a new `Product.custom_field_values` `JSONField(default=dict)`, keyed by the definition's pk (stable
across a later rename), no dynamic-schema library involved — the same bare-JSONField-for-flexible-data precedent
already used by `AuditEvent.metadata`/`GeneratedDocument.context_snapshot`. `ProductForm.__init__` appends one field
per currently-active definition (never hardcoded), so the product create/edit screen always reflects whatever
Administrators have defined; `apps.catalog.services._validate_custom_field_values()` keeps only keys matching a
currently-active definition and coerces each value into a JSON-safe shape (a `DateField` hands back a
`datetime.date`, which `JSONField` can't store directly — coerced via `.isoformat()`) — an unrecognized key is
silently dropped rather than raised, the same allow-list philosophy `apps.core.sorting.SortableListMixin` already
established for user-controlled-but-safe field selection. `update_product()` merges rather than replaces: every
existing key belonging to a definition that's since gone inactive is left untouched (it wasn't part of the
submission — the form only ever renders active definitions), while every currently-active key reflects exactly
what was just submitted, including being cleared if left blank.

Verified live end-to-end via `manage.py shell` + `django.test.Client` against the dev database (migration applied;
defined "Warranty expiry" via the new Settings-linked screen; it appeared on the product create form under a
"Custom fields" heading; a submitted value round-tripped correctly keyed by the definition's pk; the edit form
showed it back as the initial value) before/alongside the pytest suite. New tests:
`tests/test_catalog_custom_fields.py` (definition CRUD and role checks, dynamic form-field
appearance/disappearance, value round-trip through create/update, date-to-ISO-string coercion, unknown-key silently
dropped, blank value not stored, a deactivated definition's value surviving an unrelated update, and the
view-level create/toggle/initial-value paths) — 19 new tests, all passing; full catalog suite (63 tests) green
alongside them. `ruff`/`black`/`makemigrations --check` clean. No changes to Quick Add Products — custom fields
stay a follow-up edit on the created product, matching that flow's existing documented philosophy for
description/notes/low-stock-threshold.

**Acceptance**: an Administrator can add a custom field from Settings and it appears on every product's create/edit
form immediately, no deployment; values persist correctly per product; deactivating a field hides it from the form
without losing previously saved values; a field's type can't be changed once created.

### Additional feature — spreadsheet-style product grid (Phase 4 of the structured-feature wave) — done

Phase 4 of the 5-phase plan (`C:\Users\cprodromou\.claude\plans\silly-sniffing-moler.md`; Phases 1–3 above). A new
"Edit in grid" screen (`ProductGridView`, linked from the Products list toolbar) lets an operator edit many
*existing* products in one dense-table submission — Brand/Model/SKU/Type/Tracking/Supplier/Active per row.

**Design deviation, flagged to the user before this phase started and left unchanged**: the request asked for a
true inline-editable grid (click a cell, it saves immediately). This codebase has zero JS/AJAX infrastructure and
no optimistic-locking/conflict-detection pattern anywhere (last-write-wins throughout), so real per-cell autosave
would mean writing untested-by-pytest JS from scratch. Built instead as the same dense-`<table>`-formset,
one-submit pattern already shipped and approved twice this session (Quick Add Products, Quick Receive) —
functionally and visually a spreadsheet, without new JS or concurrency risk.

`apps.catalog.forms.ProductGridRowForm` (hidden `id` + the 7 editable fields) /`ProductGridFormSet`
(`formset_factory(..., extra=0)` — editing only, no blank rows) mirror `QuickAddProductRowForm`'s shape.
`ProductGridView.get()` reuses `ProductListView`'s search/`show_inactive` filtering (factored out into a new
`_filtered_products()` helper both views now share) capped at `GRID_ROW_LIMIT = 50` rows, so a bulk-seeded
database's full catalog never loads into one formset — an operator narrows down what they're editing via the same
search box first. `post()` calls the existing `update_product()` once per row that actually changed (a per-row
comparison against the 7 grid fields' current DB values decides "unchanged" vs. "updated" — an unchanged row is
never saved and never generates an audit event), and short-circuits to a `locked` outcome *before* attempting the
service call when a row tries to change `tracking_method` on a product that already `has_movements()` (the
existing, unchanged lock in `update_product()`), rather than relying on catching its `ValidationError` — that gives
a specific "Locked" badge instead of a generic error. Crucially, the grid always forwards the product's *current*
`description`/`default_notes`/`low_stock_threshold`/`custom_field_values` back into `update_product()` unchanged
(fields the grid deliberately doesn't expose, matching Quick Add's same "follow-up edit on the single-product form"
scope decision) — passing service defaults instead would have silently wiped those fields on every grid save, a
real bug caught and fixed during design before any test was written.

Verified live end-to-end via `manage.py shell` + `django.test.Client` against the dev database (search-filtered
GET rendered the dense grid with hidden row ids; a changed supplier field POSTed correctly, reported "Updated,"
and persisted) before/alongside the pytest suite. New tests: `tests/test_catalog_views.py::TestProductGridView`
(role checks, prefilled rows, a changed row updates and persists, an untouched row reports "unchanged" and isn't
saved, a tracking-method change on a moved product reports "locked" and isn't saved, the 50-row cap using
`bulk_create` for speed) — 7 new tests; full catalog suite (70 tests) green alongside them. `ruff`/`black`/
`makemigrations --check` clean — no model changes, no migration.

**Acceptance**: an operator can open a filtered set of existing products as one editable grid, change several
cells across several rows, and save them all in one submission; only genuinely changed rows are written/audited;
a locked tracking-method change is reported per-row without failing the rest of the batch; fields the grid doesn't
expose are never touched by a grid save.

### Additional feature — ad-hoc report builder (Phase 5 of the structured-feature wave) — done

Phase 5, the last and highest-risk phase of the 5-phase plan
(`C:\Users\cprodromou\.claude\plans\silly-sniffing-moler.md`; Phases 1–4 above). Any authenticated user can now
build their own report — pick a data source, pick columns, add up to three filters, save it — instead of being
limited to the 13 fixed reports in `apps.reporting.queries`. Reachable from the Reports hub's new "Custom reports"
section.

**The one requirement that could not be compromised on**: a user-composed report must never be able to see data
outside the viewer's accessible locations — the same guarantee every fixed report already gives
(`apps.reporting.queries`'s module docstring). New `apps/reporting/report_builder.py` is the single place a
`SavedReport` (or an in-progress selection) ever becomes a real queryset:
`build_queryset(user=..., base_model=..., selected_fields=..., filters=...)` always calls the right *existing*
scoping function for that model — `apps.locations.scoping.scope_queryset()` (Assets, Stock Balances),
`apps.inventory.access.scope_transaction_queryset()` (Transactions), or `scope_asset_status_history_queryset()`
(Asset Status History) — **before** a single user-controlled field or filter is ever applied. Transaction Lines had
no existing scoping helper (nothing needed one before), so `apps.inventory.access.scope_transaction_line_queryset()`
was added there, alongside its two siblings, mirroring `scope_asset_status_history_queryset()`'s shape exactly
(from_location/to_location, direct FKs on the line itself) rather than inventing ad-hoc location-filtering logic
inside the reporting app.

**Field/filter safety**: `REPORTABLE_FIELDS` is a fixed `{base_model: {friendly_key: orm_path}}` allow-list dict —
the exact same "user supplies a key, never a raw ORM path" pattern `apps.core.sorting.SortableListMixin` already
established for sortable columns. A selected field or filter key not present in this dict for the chosen
`base_model`, or a filter operator outside `ALLOWED_FILTER_OPS` (exact/icontains/gte/lte/in), is silently dropped —
never interpolated into `.filter(**{...})` — both when the report actually runs (`build_queryset()`) and again,
independently, when it's saved (`apps.reporting.services.create_saved_report()`); a `SavedReport` row is never
trusted as its own authorization boundary. `is_shared` (visible to every user, not just its creator) can only be
set by an Administrator — enforced in the service layer, silently forced back to `False` otherwise, not merely
hidden in the form. `selected_fields`/`filters` are plain `JSONField`s (no schema library), the same precedent
`apps.documents.DocumentTemplate` and `apps.catalog.Product.custom_field_values` already established.

**Two real bugs found and fixed while wiring up the query, before any of this reached a template**: (1)
`Product.custom_field_values`-style JSONField default checking doesn't apply here the same way —
`SavedReport.selected_fields`/`filters` needed `blank=True` added, since Django's `full_clean()` treats an empty
list `[]` as "blank" by default and rejects a perfectly valid "no filters chosen" report. (2) The first
implementation renamed each `.values()` row's key to the friendly field name via `.values(key=F(orm_path))` —
Django's `annotate()` refuses any alias that collides with a real field name already on the model (`"status"` on
`UnitAsset`, concretely), raising `ValueError: The annotation 'status' conflicts with a field on the model`, not a
silent problem. Fixed by using plain positional `.values(*orm_paths)` (never subject to that alias-collision
check) and adding a small `friendly_rows()` helper that remaps a queryset's raw ORM-path-keyed rows into
friendly-keyed ones only after slicing/capping — `build_queryset()` itself stays a genuinely lazy, sliceable
queryset throughout, which the row cap below depends on.

**Two-step builder UI, deliberately not one page**: this codebase has no JS to refresh a dependent dropdown's
options in place, so choosing which columns/filters are available (they depend on the data source) needed its own
step (`ReportBuilderStartView` picks the model, redirects to `ReportBuilderView?base_model=...`) rather than one
form with a client-side cascading select. Filters are a fixed 3-slot `formset_factory` (matching this codebase's
established fixed-slot pattern for optional bulk rows — `apps.catalog.forms.QuickAddProductRowForm`), not a
JS-driven "add another filter" button; a row with no field chosen is silently skipped. Saving immediately runs and
redirects to the report (no separate preview step) — the simplest thing that's actually useful, matching this
session's "must be useful, not time consuming" guidance. Results are capped at `SAVED_REPORT_ROW_CAP = 1000` rows
(no pagination UI for a first version) for both the HTML table and CSV export, with a visible "showing the first
1,000 rows" notice when truncated.

Verified live end-to-end via `manage.py shell` + `django.test.Client` against the dev database's real bulk-seeded
data (8,000+ assets) — built a report filtered to `status=in_stock` with three columns, saved it, confirmed the
HTML table and the CSV export both showed correctly filtered, correctly-labeled results — before/alongside the
pytest suite. New `tests/test_reporting_builder.py` (34 tests): `scope_transaction_line_queryset()` directly
(administrator sees everything, a scoped user sees only granted lines, a user with no grant sees nothing);
`build_queryset()` scoping parity for Assets and Transactions (the two different scoping code shapes), unknown
field/filter keys and disallowed operators silently dropped rather than raised, the `icontains`/`in` operators,
every one of the 5 base models producing a runnable queryset; `create_saved_report()`/`delete_saved_report()`
authorization (any user can create unshared reports, only an Administrator can share, only the owner or an
Administrator can delete); the full view-level flow including a private report 404ing for a non-owner, a shared
report staying visible, and CSV export. Full existing report suite (`tests/test_reporting.py`, 21 tests) and
transaction/scoping suites (`tests/test_inventory_transaction_access.py`, `tests/test_scoping.py`) green alongside
it — zero changes to any of the 13 existing fixed reports. `ruff`/`black`/`makemigrations --check` clean.

**Acceptance**: any user can build and save a custom report over Assets/Stock Balances/Transactions/Transaction
Lines/Asset Status History, choosing columns and up to three filters; a saved report can be shared (Administrator
only) or kept private; running any custom report — including someone else's shared one — never surfaces a single
row outside the viewer's own accessible locations, proven by test, not just by construction.

### Additional feature — Stock Purpose, custodian tracking, guided receiving, import defaults, low-stock filtering — done

Added directly on user request: "give the Stock Manager practical control over receiving, locating, classifying,
monitoring, assigning, delivering, and returning stock." Research at the start of this wave found the app already
covered most of the ask — Assign/Deliver/Return/Reserve/Damaged/Lost/Dispose/Admin-correction/reversal and a
staged-then-executed Excel/CSV import pipeline all pre-existed as real audited services — so this wave is five
targeted additions, not a rebuild:

**1. Stock Purpose (Internal/Customer) — genuinely new, not in the spec or any architecture doc.** A
`StockPurpose` classification (`apps/inventory/models.py`), orthogonal to `UnitStatus` exactly as requested ("an
item can be Internal + In Stock, Customer + Reserved, Customer + Delivered"). The one real design decision: whether
`StockBalance` needed to split by purpose for quantity-tracked stock, or whether tagging transactions/reservations
alone would do. Chose to split — `StockBalance`'s unique key moved from `(product, location)` to
`(product, location, stock_purpose)` — because "Internal Stock"/"Customer Stock" filtered views need a real
available-quantity number per bucket for bulk products, not just serialized ones, and a tag-only approach can't
produce that. Migrated with a plain `AddField(default="internal")`, so every pre-existing balance keeps today's
exact numbers in a single Internal bucket; a second Customer bucket only appears where a Stock Manager actually
creates one via receipt or the new reclassify action. `StockReservation` and `InventoryTransactionLine` carry the
same field so a reservation/line always names which bucket it moved. New `apps/inventory/services/purpose.py`:
`reclassify_unit_purpose()` (a label change + `AuditEvent.STOCK_PURPOSE_CHANGED`, no ledger transaction — nothing
physically moved) and `reclassify_quantity_purpose()` (a real two-leg `MovementType.PURPOSE_CHANGE` transaction,
symmetric to `bulk_transfer()` but changing purpose instead of location, reusing `adjust_balance()`'s existing
negative/available-stock guard). Every service that touches a `StockBalance`/`StockReservation`
(`assignments.py`, `returns.py`, `reservations.py`, `disposition.py`, `transfers.py`, `corrections.py`) was
threaded with a `stock_purpose` kwarg defaulting to Internal, so no existing call site's behavior changed.

**2. A denormalized "current custodian" pointer.** `employee_name`/`final_customer`/`project_reference`/
`expected_return_date` already existed on `InventoryTransaction` (doc 02), but only reachable by joining through
transaction lines — there was no fast "who has this right now" for the grid/detail/search the request asked for.
Added exactly one FK, `UnitAsset.current_custody_transaction`, plus one new free-text field,
`InventoryTransaction.recipient_reference` ("employee/customer reference if available"). Every other requested
display field (type, name, reference, project reference, transaction number, date, expected return, notes) is
derived from that one FK at read time (`apps.inventory.views._assigned_to_block()`), not duplicated onto new
columns. Deliberately **not** a Customer/Employee/Contact master-data entity — spec §22 explicitly excludes
"Customer addresses and contacts" and "Customer/project master-data management," and §2.6 states the app "is not
the master system for customer or project data"; `recipient_reference` stays manual free text, same as
`employee_name`/`final_customer` already are. The pointer is set/cleared in exactly one place —
`apps.inventory.services.ledger.write_unit_line()` — rather than scattered across every caller: it's set whenever
`to_status` is Assigned/Delivered and cleared whenever `from_status` was Assigned/Delivered, which correctly
covers assignment, delivery, return, a direct damaged/lost/dispose from Assigned, and an Administrator correction
moving an asset in or out of custody, all through the one shared primitive every unit status change already goes
through.

**3. A true atomic multi-line goods receipt.** `receive_stock()`/`receive_stock_batch()` (existing) handle one
product per call, and the batch variant is explicitly not atomic across rows (one `receive_stock()` call per
serial). New `receive_stock_bulk()` (`apps/inventory/services/receipts.py`) takes several product lines — mixed
serialized/quantity — under one shared default location/purpose with a per-line override, validates every line
(product active, tracking-method shape, a batch-wide duplicate-serial pre-check) before writing anything, then
writes one `InventoryTransaction` with N lines inside a single `@transaction.atomic` block: a bad line anywhere
rolls back the entire receipt. New `ReceiveBulkView`/`inventory:receive_bulk`, reusing the
`QuickAddProductFormSet` dynamic-formset pattern (`apps/catalog/forms.py`) for the line rows; the rendered,
still-filled formset doubles as the review step before commit, matching how every other movement form in this app
already surfaces validation problems.

**4. Import defaults.** `ImportBatch` gained `default_location`/`default_stock_purpose`, set once at upload
(`ImportUploadForm`) and applied by `_stage_row()` only when a row's own LOCATION/Stock Purpose columns don't
resolve — per-row values still win, matching the same "batch default with per-item override" shape the new bulk
receive screen uses for manual entry. Added an optional `Stock Purpose` column to the import template/parser and a
parallel `.xlsx` template download (`build_template_xlsx()`, openpyxl — already a dependency) alongside the
existing CSV one. The pre-existing `file_checksum` repeat-upload check (doc 07) stayed advisory at upload time (a
`ValidationError` there would lose the user's file selection with no server-side way to resubmit without
reselecting it) but gained a hard confirmation gate at `ImportExecuteView` — the point where inventory is actually
about to change — re-checked at execute time (not just upload time) so a batch left staged for a while is still
caught.

**5. Low-stock discoverability, not a new notification mechanism.** The threshold field, `low_stock_balances()`,
`dashboard_summary()`'s count, and a dedicated `reporting:low_stock` page all already existed (spec §16 —
"disabled unless configured," "no notification service required initially"; §22 excludes "Mandatory minimum-stock
alerts"). "One active warning, no duplicates, resolves automatically" is satisfied for free by a live query — one
row per below-threshold `(product, location, purpose)` triple, impossible to duplicate because nothing is stored,
resolved the instant the query re-runs after stock rises — so no new model, scheduled task, or delivery channel
was added. `LowStockView` gained a country/location filter (any level, via the same `path__descendant_or_self`
ltree match every other location filter in this app already uses) and a Stock Purpose column; the dashboard
(`apps/core/views.py`'s `HomeView`, unchanged — `dashboard_summary()`/`data_quality_summary()` already flow
through automatically) gained Internal/Customer/Reserved/Assigned/Delivered/Disposed stat cards, Receive/Import/
Transfer/Assign/Deliver quick actions, and a new "Assigned/Delivered assets missing custodian info" data-quality
line (the same "genuine data-integrity gap, not a normal state" framing as the pre-existing unlocated-assets
check).

**Grid/report surfacing**: `UnitAssetGridDataView`/`StockBalanceGridDataView` gained `stock_purpose`/
`stock_purpose_display` and (assets only) an `assigned_to` block, with a Stock Purpose header-filter column on
both grids and an Assigned To column on the Assets grid — **not** added to `EDITABLE_FIELDS`/any inline-edit path,
since purpose and custodian changes must go through the audited reclassify/movement views, per this feature
request's own explicit "no direct grid editing of ... status, location, recipient, assignment" rule.
`employee_assignments`/`customer_deliveries` reports gained a Reference column.

**Testing**: 41 new tests across `test_inventory_receipts.py` (`receive_stock_bulk` — mixed lines, default+
override location/purpose, mid-batch rollback, cross-line duplicate-serial detection), `test_inventory_
assignments_deliveries.py` (custody pointer set on assign/deliver, `recipient_reference`, cleared on return and on
a direct mark-lost from Assigned), new `test_inventory_purpose.py` (unit reclassify audit trail and no-ledger-
write guarantee, quantity reclassify atomicity and the existing available-stock guard rejecting an over-large
move, reserved quantity correctly blocking a reclassify), `test_inventory_grid.py` (new serialized fields, new
fields absent from every editable-field allow-list check), `test_dashboard.py` (new stat keys, the missing-
custodian data-quality check), `test_reporting.py` (low-stock location filter including country-level ancestor
match), and `test_imports_services.py`/`test_imports_views.py` (default location/purpose fallback and per-row
override, the `.xlsx` template round-tripping through the existing parser, the repeat-upload confirmation gate).
Full suite (746 tests, up from 705) green; `ruff`/`black`/`makemigrations --check --dry-run` clean. Verified live
end-to-end against the dev database via Selenium (both themes' shared component classes, no new CSS needed): a
multi-line bulk receipt (one serialized + one quantity line) submitted correctly to one transaction, the created
asset's detail page showed the Stock Purpose badge and Reclassify action, the dashboard's new stat cards and
data-quality line rendered with real counts, and the Low Stock report's location filter narrowed results
correctly including via a Country-level ancestor.

**Acceptance**: a Stock Manager can receive a mixed multi-product batch as one atomic transaction with a shared
default location/purpose and per-row overrides; every asset/balance can be classified Internal or Customer
independently of its operational status, with every change audited; the grid, asset detail page, and reports show
who currently holds an assigned/delivered asset without a per-row query; an Excel/CSV import can set a batch
default location/purpose and round-trips through either template format; the Low Stock report can be filtered by
country or location; none of this touches the append-only ledger tables' write path except through the existing
`ledger.py` primitives, and no existing URL, permission, or transaction record was preserved with anything other
than its exact pre-existing behavior on all migrated data. **Deferred, not built**: `apps/imports` remains
Administrator-only end to end, even though `docs/architecture/04-permission-matrix.md` documents Stock Manager
import access "within scope" — opening it up safely needs location-scoping added to the whole import pipeline
first (an `ImportBatch`/`ImportRow` currently has no location field to scope by until a row is staged), which is
a separable follow-up, not attempted here to avoid a half-built permission change.

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
