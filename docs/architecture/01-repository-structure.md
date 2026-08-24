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
│   ├── audit/                         # AuditEvent, audit-logging service used by every other app
│   └── reporting/                     # read-only report/export views built on the other apps' models
├── templates/                         # base layout + per-app template overrides (apps also keep local templates/)
├── static/
├── tests/                             # cross-app / end-to-end browser tests (per-app unit tests live in each app)
├── deploy/
│   ├── docker-compose.yml
│   ├── docker-compose.prod.yml
│   ├── Dockerfile
│   └── nginx/ (reverse-proxy example)
├── scripts/                           # backup.sh, restore.sh, seed_dev_data management command wrappers
├── manage.py
├── pyproject.toml
├── .env.example
├── CLAUDE.md
└── AGENTS.md
```

## App responsibilities

| App | Owns | Depends on |
|---|---|---|
| `core` | Abstract base models (`TimestampedModel`, `UserStampedModel`), the scope-checking query layer, health/readiness endpoint, shared exceptions, pagination helpers | — |
| `accounts` | `User` (Django's, extended via a `Profile` for display name only — **not** for role), Groups (`Administrator`, `StockManager`, `ReadOnlyUser`), `UserLocationAccess` | `core`, `locations` |
| `locations` | `Location` hierarchy, activation/deactivation, hierarchy validation | `core` |
| `catalog` | `Brand`, `ProductType`, `Product`, duplicate-product detection service | `core` |
| `inventory` | `UnitAsset`, `StockBalance`, `StockReservation`, `InventoryTransaction`, `InventoryTransactionLine`, `AssetStatusHistory`, and every movement service (receipt, transfer, reservation, assignment, delivery, return, damage, loss, disposal, correction, reversal) | `core`, `locations`, `catalog`, `accounts`, `audit` |
| `documents` | `GeneratedDocument`, `Attachment`, HTML→PDF rendering, protected download views | `core`, `inventory`, `accounts`, `audit` |
| `imports` | `ImportBatch`, `ImportRow`, staging/validation/execution services for the legacy Excel format | `core`, `catalog`, `locations`, `inventory`, `audit` |
| `audit` | `AuditEvent` model and the `record_event(...)` service every other app calls; append-only enforcement | `core`, `accounts` |
| `reporting` | List/filter/report/export views and query objects; **no models of its own** — only reads through the scope layer | all of the above (read-only) |

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
