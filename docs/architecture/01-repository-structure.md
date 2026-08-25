# Repository and Django App Structure

## Top-level layout

```
stock_inventory/
├── docs/
│   ├── Stock_Inventory_Application_Build_Specification.md
│   ├── Stock_Inventory_Codex_Claude_Prompt_Pack.md
│   └── architecture/                  # this folder
├── config/                            # Django project package (settings, root urls, wsgi/asgi)
│   ├── settings/
│   │   ├── base.py
│   │   ├── dev.py
│   │   ├── test.py
│   │   └── production.py
│   ├── urls.py
│   └── wsgi.py / asgi.py
├── apps/
│   ├── core/                          # shared base models, mixins, scope query layer, health check
│   ├── accounts/                      # User extensions, roles (Groups), UserLocationAccess
│   ├── locations/                     # Location hierarchy (Country → ... → Shelf/Bin)
│   ├── catalog/                       # Brand, ProductType, Product
│   ├── inventory/                     # UnitAsset, StockBalance, StockReservation, ledger, movement services
│   ├── documents/                     # GeneratedDocument, Attachment, PDF rendering
│   ├── imports/                       # ImportBatch, ImportRow, staged Excel/CSV import
│   ├── exports/                       # ExportSettings, scheduled full-inventory Excel snapshot to a local/network path
│   ├── audit/                         # AuditEvent, audit-logging service used by every other app
│   └── reporting/                     # read-only report/export views built on the other apps' models
├── templates/                         # base layout + per-app template overrides (apps also keep local templates/)
├── static/
├── tests/                             # the full pytest suite (per-app service/view tests + cross-app/end-to-end)
├── deploy/
│   ├── docker-compose.yml             # local dev
│   ├── docker-compose.prod.yml        # production: no code bind-mount, nginx reverse proxy
│   ├── Dockerfile / Dockerfile.prod
│   ├── nginx.conf.example
│   ├── backup.sh / RESTORE.md / DEPLOYMENT.md
│   └── sql/hardening_runtime_role.sql # optional DB-level defense in depth (doc 08)
├── .github/workflows/ci.yml           # ruff/black/migration-check/pytest on every push, real Postgres service
├── manage.py
├── pyproject.toml
├── .env.example / .env.production.example
├── CLAUDE.md
└── AGENTS.md
```

## App responsibilities

| App | Owns | Depends on |
|---|---|---|
| `core` | Abstract base models (`TimestampedModel`, `UserStampedModel`, `UUIDPrimaryKeyModel`), role/Group authorization helpers (`authorization.py`: `is_administrator`, `require_role`, `RoleRequiredMixin`), a template context processor exposing role info for nav hiding, health/readiness endpoint | — |
| `accounts` | `User` (Django's, extended via a `Profile` for display name only if ever needed — **not** for role), Groups (`Administrator`, `StockManager`, `ReadOnlyUser`), `UserLocationAccess`, login success/failure audit signals | `core`, `locations`, `audit` |
| `locations` | `Location` hierarchy, activation/deactivation, hierarchy validation, **and the location-scope authorization layer** (`scoping.py`: `accessible_locations`, `scope_queryset`, `require_location_access`) | `core`, `accounts` (imported narrowly, inside function bodies, to read `UserLocationAccess`) |
| `catalog` | `Brand`, `ProductType`, `Product`, duplicate-product detection service | `core` |
| `inventory` | `UnitAsset`, `StockBalance`, `StockReservation`, `InventoryTransaction`, `InventoryTransactionLine`, `AssetStatusHistory`, every movement service (receipt, transfer, reservation, assignment, delivery, return, damage, loss, disposal, correction, reversal), and `access.py`'s `require_transaction_access` (location-scope check for a transaction, keyed off header *or* line locations — see doc 06) | `core`, `locations`, `catalog`, `accounts`, `audit` |
| `documents` | `GeneratedDocument`, `Attachment`, HTML→PDF rendering (WeasyPrint), protected download views | `core`, `inventory`, `audit` |
| `imports` | `ImportBatch`, `ImportRow`, staging/validation/execution services for the legacy Excel format | `core`, `catalog`, `locations`, `inventory`, `audit` |
| `audit` | `AuditEvent` model and the `record_event(...)` service every other app calls; append-only enforcement (blocks instance and bulk update/delete at the ORM layer) | `core` |
| `reporting` | List/filter/report/export views and query objects; **no models of its own** — only reads through the scope layer | all of the above (read-only) |

The scope-checking layer described below as "`core.scoping`" in the original plan was implemented as `apps.locations.scoping` instead — it necessarily depends on `Location` and `UserLocationAccess`, and keeping `core` free of that dependency (per this table) mattered more than keeping the name literally matching the plan. `locations` and `accounts` have a small mutual dependency (`accounts.UserLocationAccess` FKs to `locations.Location`; `locations.scoping` reads `accounts.UserLocationAccess`) — resolved by importing `UserLocationAccess` inside `scoping.py`'s function bodies rather than at module load time, so there's no import-time circularity.

`InventoryTransaction`'s own location-scope check (`apps.inventory.access.require_transaction_access`) is a separate, small layer on top of `locations.scoping.require_location_access` — a transaction only carries a header-level `source_location`/`destination_location` for receipt, transfer, and return; every other movement type (assignment, delivery, reservation, disposal, correction...) only has location information on its *lines*. `require_transaction_access` checks both header and line locations so nothing built on it (the transaction detail view, and `apps.documents`' generate/download views) under-scopes those movement types. This was found and fixed during Phase 5 — see doc 09's Prompt 5 note.

### Why this split

- `inventory` is intentionally the largest app because the spec is explicit that movement rules must be transactional and centralized (spec §19) — splitting receipts/transfers/assignments/etc. into separate apps would force cross-app transactions and duplicate the ledger-writing logic. Movement *services* are separate Python modules inside `inventory/services/` (one file per workflow) so the app stays navigable; they share one `inventory/services/ledger.py` that is the only code allowed to write `InventoryTransaction`/`InventoryTransactionLine`/`AssetStatusHistory`/`StockBalance` rows.
- `reporting` has no models because every report is a read over `inventory`/`catalog`/`locations` data filtered through the same scope layer used by the main screens — this guarantees a report can never leak data a list view would block (spec §4, §15).
- `audit` is separate from `core` (rather than folded in) because it has its own storage/immutability rules (append-only, no update/delete) that are easier to enforce and unit-test in isolation.

## Cross-cutting rule: services, not views/signals

Every app that mutates state exposes a `services.py` (or `services/` package) with plain functions/classes that:

1. Open one atomic transaction (`transaction.atomic()`).
2. Take row-level locks (`select_for_update()`) on any `StockBalance`/`UnitAsset` rows they will change, before reading current values, to prevent concurrent overspend (spec Prompt 4 requirement).
3. Validate business rules (status transitions, scope, tracking method, quantity).
4. Write ledger rows (`InventoryTransaction` + lines), update denormalized current-state columns (`UnitAsset.status`, `StockBalance.on_hand_quantity`), and call `audit.record_event(...)` — all inside the same transaction.
5. Return a typed result object the view renders.

Views/forms only: authenticate, authorize (via the scope layer), parse input, call one service function, render the result. No Django signals are used for balance or status changes, so there is exactly one code path per movement type and no hidden side effects (spec Prompt 3 requirement).

## `CLAUDE.md` / `AGENTS.md`

Both files will be created in Phase 1 (Prompt 1) and kept in sync, pointing at:
- this architecture folder as the source of design decisions,
- the spec as the source of business rules,
- the actual dev commands once they exist (`docker compose up`, `pytest`, `manage.py migrate`, lint/format commands),
so a future session of either tool starts from the same context.
