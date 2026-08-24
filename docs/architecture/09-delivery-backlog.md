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

### Prompt 6 — Excel/CSV import

Depends on Prompt 3 (products/assets) and Prompt 2 (locations). Can run in parallel with Prompt 4/5 once those
dependencies are met, since import reuses — but does not block — the interactive movement services. Best sequenced
after a sanitized copy of the real legacy workbook is available (prompt pack note).

**Acceptance**: acceptance criterion §21.14; fixture-workbook tests for malformed headers, duplicate rows/serials,
mixed date formats, interrupted/retried batches.

### Prompt 7 — Dashboard, search, reports, disposal reporting

Depends on Prompt 4 (movements exist to report on) and Prompt 5 (documents referenced from transaction/report
views). Delivers: all filters from spec §14, all reports from spec §15, low-stock threshold config/dashboard
(disabled unless configured), disposed-HDD-optimized report.

**Acceptance**: acceptance criteria §21.10, §21.15; query-count/perf tests against the 8,000+-row seed dataset.

## Phase 4 — Migration and hardening

### Prompt 8 — Security, performance, backup, production deployment

Depends on everything above existing to harden. Delivers: throttling, session/password policy finalized, index
review against real seeded volume, backup/restore scripts + verified restore, production Compose + reverse-proxy
guidance, deployment runbook.

**Acceptance**: restore procedure actually executed once in a disposable environment; measured (not assumed)
query timings reported against 8,000+ seeded records.

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
  data — imported rows) so it is last among the "build" prompts.
- Prompts 8 and 9 are hardening/audit passes over the whole system and must come last.
