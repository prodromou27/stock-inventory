# Architecture Plan — Index

Status: **Built and audited.** All nine prompt-pack prompts (plus two additional features added on direct user
request — Excel export scheduling and GitHub CI — see doc 09) are implemented, tested, and released; see
[11-traceability-matrix.md](11-traceability-matrix.md) for the final acceptance audit against spec §21/§22. This
package started as the Prompt 0 technical plan and has been kept up to date through every phase since — where an
implementation detail diverged from the original plan, the relevant document below was updated in place, not left
to go stale.

Source of truth: [`../Stock_Inventory_Application_Build_Specification.md`](../Stock_Inventory_Application_Build_Specification.md)
(section references below are to that document).

This folder contains the technical plan the build followed. Read in this order:

1. [01-repository-structure.md](01-repository-structure.md) — Django app layout and module responsibilities
2. [02-data-model.md](02-data-model.md) — entity-relationship model, field-level design, indexes, constraints, deletion policy
3. [03-status-and-movement-rules.md](03-status-and-movement-rules.md) — status-transition table and movement/service rules
4. [04-permission-matrix.md](04-permission-matrix.md) — role × location-scope permission matrix and enforcement strategy
5. [05-tracking-and-duplicates.md](05-tracking-and-duplicates.md) — unit vs. quantity tracking, duplicate vendor serial and duplicate product strategy
6. [06-documents-and-snapshots.md](06-documents-and-snapshots.md) — immutable PDF snapshot and attachment strategy
7. [07-excel-import.md](07-excel-import.md) — staging, validation, idempotency, rollback
8. [08-nonfunctional-plan.md](08-nonfunctional-plan.md) — security, backup, testing, observability, performance
9. [09-delivery-backlog.md](09-delivery-backlog.md) — phased backlog with dependencies and acceptance criteria, and the as-built record of what each phase actually delivered
10. [10-assumptions-and-open-questions.md](10-assumptions-and-open-questions.md) — every place this plan had to fill a gap the spec left open, split into non-blocking assumptions vs. true blockers
11. [11-traceability-matrix.md](11-traceability-matrix.md) — final release audit: every spec §21/§22 item mapped to real, currently-passing code and tests

## Guiding constraints carried through every document

- Django 5.x modular monolith, server-rendered templates, HTMX/Alpine only where it removes a full page reload, PostgreSQL 16+, WeasyPrint for PDF, Docker Compose. No SPA, no Celery/Redis, no public API — none of the requirements in the spec currently demand them (spec §3).
- Inventory state is always an outcome of a recorded, transactional movement — never a field a user edits directly (spec §2.1, §12).
- All authorization is enforced server-side, on every read and write, via one shared scope-checking layer — never in templates or hidden UI alone (spec §4).
- Business rules live in application services called by both views and (future) any API — not in views, forms, signals, or templates (spec §19).

## Blocking questions

**None.** Every gap found while producing this plan was resolvable with a documented, low-risk default that doesn't foreclose spec §24's explicitly-deferred decisions. See [10-assumptions-and-open-questions.md](10-assumptions-and-open-questions.md) for the full list and reasoning — worth a skim before Phase 1 starts, since a couple of the defaults (quantity-stock reservation model, transaction numbering) are judgment calls a reviewer may want to redirect.

## Release status

See [11-traceability-matrix.md](11-traceability-matrix.md) for the release recommendation and the full acceptance
audit. In short: every §21 criterion is implemented and covered by a passing test, every §22 exclusion is confirmed
genuinely absent, and the known gaps (browser-level UI tests, the production Docker Compose topology not yet
booted end to end) are documented there rather than silently left out.
