# Phased Delivery Backlog

Expands spec §20's four phases into concrete backlog items with dependencies and acceptance criteria, aligned with
the prompt pack's Prompts 1–9 (each prompt below = one reviewed increment; per spec §23.1, none of these are run as
one unattended change).

## Phase 1 — Foundation (prompt pack Prompt 1)

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

### Prompt 2 — Locations, users, scoped permissions

Depends on Phase 1. Delivers: `Location` model + hierarchy validation trigger, `UserLocationAccess`, the three
Groups, `core.scoping` layer, location admin/list/detail screens, audit events for location/role/access changes.

**Acceptance**: acceptance criterion §21.3 test suite (direct-URL scope bypass attempts fail) passes; deactivating
a referenced location keeps it visible in history; seed data includes a realistic one-country/one-building/second-
floor/room/rack/shelf tree.

### Prompt 3 — Product catalog and inventory ledger

Depends on Prompt 2 (needs `Location`, scoping, roles). Delivers: `Brand`, `ProductType`, `Product`,
duplicate-product detection, `UnitAsset` + duplicate-serial detection/acknowledgement, `StockBalance`,
`InventoryTransaction`/`InventoryTransactionLine` ledger tables and the shared `ledger.py` writer, receipt as the
first working movement (needed to get any data into the system at all), product/asset admin and list/detail/history
UI.

**Acceptance**: acceptance criteria §21.1 (receive both tracking types) and §21.2 (duplicate serial acknowledgement
visible and audited); tracking-method lock enforced by test.

### Prompt 4 — Movement workflows

Depends on Prompt 3. Delivers: reservation, reservation release, bulk transfer, employee assignment, customer
delivery, partial/complete return, return assessment, mark damaged, mark lost, disposal, admin correction,
reversal — all via `inventory/services/`, all row-locked, all producing confirmation pages showing the transaction
number.

**Acceptance**: acceptance criteria §21.4–§21.9, §21.11, §21.12; the full status-transition table (doc 03) covered
by tests including every invalid-transition rejection.

## Phase 3 — Documents and reporting

### Prompt 5 — Printable forms, PDFs, attachments

Depends on Prompt 4 (needs completed assignment/delivery transactions to render). Delivers: `GeneratedDocument`,
WeasyPrint rendering from line snapshots, sequential document numbering, `Attachment` upload/download with
authorization.

**Acceptance**: acceptance criterion §21.13; a product edit after document generation does not change a previously
rendered PDF (tested by regenerating and diffing, or by asserting the snapshot fields are untouched).

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
