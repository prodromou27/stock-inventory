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

Phase 2 (locations/users/permissions) and Phase 3 (product catalog and inventory ledger) are in place: the
`Location` hierarchy, role/location-scope authorization, a user-access management screen, the product catalog
(with duplicate-Brand/Model/SKU detection), and receiving stock — both serialized (`UnitAsset`, with duplicate
vendor-serial detection/acknowledgement) and quantity-tracked (`StockBalance`) — onto an immutable
`InventoryTransaction`/`InventoryTransactionLine` ledger. See
[`docs/architecture/09-delivery-backlog.md`](docs/architecture/09-delivery-backlog.md) for the full delivery plan;
the remaining movement workflows (transfer, reservation, assignment, delivery, return, damage/loss/disposal,
corrections) begin in Phase 4.
