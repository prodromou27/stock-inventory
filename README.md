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

Phases 1–4 are in place: Docker/PostgreSQL foundation and auth; the `Location` hierarchy and role/location-scope
authorization; the product catalog with duplicate-Brand/Model/SKU detection; and the full inventory ledger —
receiving stock (serialized and quantity-tracked, with duplicate vendor-serial detection), bulk location transfer,
reservation/release, employee assignment, customer delivery, partial/complete returns with assessment, marking
assets damaged/lost/disposed, and Administrator corrections/reversals, all as an immutable
`InventoryTransaction`/`InventoryTransactionLine` ledger. See
[`docs/architecture/09-delivery-backlog.md`](docs/architecture/09-delivery-backlog.md) for the full delivery plan
and this phase's noted scope simplifications; documents/PDF generation and reporting/search begin in Phase 5+.
