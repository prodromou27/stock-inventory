# Stock Inventory

[![CI](https://github.com/prodromou27/stock-inventory/actions/workflows/ci.yml/badge.svg)](https://github.com/prodromou27/stock-inventory/actions/workflows/ci.yml)

Internal stock and technology-asset inventory application, replacing an Excel-based process. See:

- [`docs/Stock_Inventory_Application_Build_Specification.md`](docs/Stock_Inventory_Application_Build_Specification.md) — business specification
- [`docs/architecture/`](docs/architecture/) — technical design (start at `docs/architecture/README.md`)
- [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md) — commands and working agreements for AI coding assistants

## Quick start (Docker)

```
cp .env.example .env
docker compose -f deploy/docker-compose.yml up
docker compose -f deploy/docker-compose.yml exec web python manage.py migrate
docker compose -f deploy/docker-compose.yml exec web python manage.py seed_dev_data
docker compose -f deploy/docker-compose.yml exec web python manage.py seed_locations
```

Then visit http://localhost:8000/ and log in with one of the seeded dev accounts (credentials are printed to the
console by `seed_dev_data`).

## Status

All of Phases 1–3 (Prompts 1–7) are in place: Docker/PostgreSQL foundation and auth; the `Location` hierarchy and
role/location-scope authorization; the product catalog with duplicate-Brand/Model/SKU detection; the full inventory
ledger (receiving, bulk transfer, reservation/release, employee assignment, customer delivery, partial/complete
returns with assessment, marking assets damaged/lost/disposed, and Administrator corrections/reversals, all as an
immutable `InventoryTransaction`/`InventoryTransactionLine` ledger); printable assignment/delivery PDFs (WeasyPrint,
immutable snapshots) plus scanned-signature attachment upload/download; full filtering/search, CSV export, an
audit-log viewer, and every report from spec §15 (current stock, stock by location, reserved stock, employee
assignments, customer deliveries, stock by project reference, temporary assignments, damaged/lost/disposed assets —
including a disposed-HDD-focused view — movement history, and low stock), verified for responsiveness at 8,000+
seeded records; and an Administrator-only Excel/CSV importer (`.xlsx`/`.csv`, staged preview with per-row
location-override, idempotent batched execution, results/template CSV downloads) for the legacy workbook migration.
Phase 4/Prompt 8 (security/performance hardening) is also in place: login throttling (`django-axes`), custom
403/404/500 error pages, DB-level defense in depth for the audit/ledger tables (a separate, lower-privilege runtime
database role), a backup/restore procedure verified against real data, and a production Docker Compose + nginx
reverse-proxy deployment path — see [`deploy/DEPLOYMENT.md`](deploy/DEPLOYMENT.md). An Administrator can also
configure a local/network path for a nightly-or-weekly full Excel snapshot of inventory (**Export Settings** in the
nav) as a human-readable safety net alongside the database backup. See
[`docs/architecture/09-delivery-backlog.md`](docs/architecture/09-delivery-backlog.md) for the full delivery plan;
Prompt 9 (the final acceptance/release audit and traceability matrix) is what remains.

PDF generation needs the GTK3 native runtime outside Docker — see `CLAUDE.md` if running without Docker on Windows.
