# Stock Inventory

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

Phases 1–5 and Phase 7 are in place: Docker/PostgreSQL foundation and auth; the `Location` hierarchy and
role/location-scope authorization; the product catalog with duplicate-Brand/Model/SKU detection; the full inventory
ledger (receiving, bulk transfer, reservation/release, employee assignment, customer delivery, partial/complete
returns with assessment, marking assets damaged/lost/disposed, and Administrator corrections/reversals, all as an
immutable `InventoryTransaction`/`InventoryTransactionLine` ledger); printable assignment/delivery PDFs (WeasyPrint,
immutable snapshots) plus scanned-signature attachment upload/download; and full filtering/search, CSV export, an
audit-log viewer, and every report from spec §15 (current stock, stock by location, reserved stock, employee
assignments, customer deliveries, stock by project reference, temporary assignments, damaged/lost/disposed assets —
including a disposed-HDD-focused view — movement history, and low stock), verified for responsiveness at 8,000+
seeded records. See [`docs/architecture/09-delivery-backlog.md`](docs/architecture/09-delivery-backlog.md) for the
full delivery plan; Excel/CSV import (Prompt 6) is the remaining "build" item, best sequenced once a sanitized copy
of the real legacy workbook is available.

PDF generation needs the GTK3 native runtime outside Docker — see `CLAUDE.md` if running without Docker on Windows.
