# Stock Inventory

[![CI](https://github.com/prodromou27/stock-inventory/actions/workflows/ci.yml/badge.svg)](https://github.com/prodromou27/stock-inventory/actions/workflows/ci.yml)

Internal stock and technology-asset inventory application, replacing an Excel-based process. See:

- [`docs/Stock_Inventory_Application_Build_Specification.md`](docs/Stock_Inventory_Application_Build_Specification.md) — business specification
- [`docs/architecture/`](docs/architecture/) — technical design (start at `docs/architecture/README.md`)
- [`docs/administrator-quickstart.md`](docs/administrator-quickstart.md) / [`docs/stock-manager-quickstart.md`](docs/stock-manager-quickstart.md) — task-oriented guides for the people who actually use the app
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

**Feature-complete and released.** Every phase of the delivery backlog (all nine prompt-pack prompts, plus two
features added directly on user request — scheduled Excel export and this GitHub/CI setup) is implemented, tested,
and audited: the full inventory ledger (receiving, transfer, reservation, assignment, delivery, returns,
damage/loss/disposal, and Administrator corrections/reversals — all as an immutable
`InventoryTransaction`/`InventoryTransactionLine` ledger); role/location-scoped authorization everywhere; printable
immutable PDF snapshots and attachment upload; full filtering/search, CSV export, an audit log, and every report
from spec §15; an Administrator-only Excel/CSV importer for the legacy workbook migration; an Administrator-only
scheduled Excel export to a local/network path as a backup safety net; and production-hardening (login throttling,
custom error pages, DB-level defense in depth for the ledger/audit tables, a verified backup/restore procedure, and
a production Docker Compose + nginx deployment path).

See [`docs/architecture/11-traceability-matrix.md`](docs/architecture/11-traceability-matrix.md) for the final
release audit — every spec §21 acceptance criterion and §22 exclusion mapped to real, currently-passing code and
tests, plus the security review and release recommendation — and
[`docs/architecture/09-delivery-backlog.md`](docs/architecture/09-delivery-backlog.md) for the full phase-by-phase
delivery history.

PDF generation needs the GTK3 native runtime outside Docker — see `CLAUDE.md` if running without Docker on Windows.
