# AGENTS.md

Instructions for AI coding agents (Codex, Claude Code, or others) working in this repository. Kept in sync with
[`CLAUDE.md`](CLAUDE.md) — update both together.

## What this project is

An internal stock/technology-asset inventory application replacing an Excel process. The authoritative business
specification is [`docs/Stock_Inventory_Application_Build_Specification.md`](docs/Stock_Inventory_Application_Build_Specification.md).
The approved technical design is under [`docs/architecture/`](docs/architecture/) — read
[`docs/architecture/README.md`](docs/architecture/README.md) first. Every model, permission, and workflow decision
in the codebase should trace back to one of those documents.

Read both before making non-trivial changes. If a change would conflict with either, stop and ask rather than
inventing a new rule (spec §23.10) — do not silently invent business rules.

## Working agreements

- Business logic lives in each app's `services.py`/`services/` module, called by views — never in views, forms,
  signals, or templates (architecture doc 01).
- Every read/write that touches locations, inventory, or reports must go through `apps.locations.scoping`
  (`accessible_locations`/`scope_queryset`/`require_location_access`) and `apps.core.authorization`
  (`require_role`/`RoleRequiredMixin`) — no view queries `Location`/`UnitAsset`/`StockBalance`/
  `InventoryTransaction` directly.
- Ledger tables (`InventoryTransaction`, `InventoryTransactionLine`, `AssetStatusHistory`, `AuditEvent`) are
  append-only. Never add code that updates or deletes rows in those tables.
- Implement one delivery-backlog phase at a time (`docs/architecture/09-delivery-backlog.md`); do not jump ahead to
  a later phase's models in the same change.
- Preserve existing user changes; do not run destructive Git operations; do not commit or push unless explicitly
  asked.

## Commands

With Docker (recommended — matches the deployment target):

```
cp .env.example .env
docker compose -f deploy/docker-compose.yml up
docker compose -f deploy/docker-compose.yml exec web python manage.py migrate
docker compose -f deploy/docker-compose.yml exec web python manage.py seed_dev_data
docker compose -f deploy/docker-compose.yml exec web python manage.py seed_locations
docker compose -f deploy/docker-compose.yml exec web python manage.py createsuperuser
docker compose -f deploy/docker-compose.yml exec web pytest
docker compose -f deploy/docker-compose.yml exec web ruff check .
docker compose -f deploy/docker-compose.yml exec web black --check .
docker compose -f deploy/docker-compose.yml exec web python manage.py makemigrations --check --dry-run
```

Without Docker (requires a local PostgreSQL 16+ with the `ltree` extension available, and a virtualenv with
`requirements-dev.txt` installed):

```
python manage.py migrate
python manage.py seed_dev_data
python manage.py seed_locations
pytest
ruff check .
black --check .
```

`seed_dev_data` and `seed_locations` both refuse to run unless `DEBUG=True` — they exist for local/dev use only.
`seed_dev_data` creates one user per role (`devadmin`/`devmanager`/`devreadonly`), with generated passwords printed
to stdout unless `SEED_ADMIN_PASSWORD`/etc. are set. `seed_locations` (run after `seed_dev_data`, which it needs an
Administrator from) creates a sample Country > Site > Floor > Storage Room > Rack > Shelf tree.

## Settings

`DJANGO_SETTINGS_MODULE` selects `config.settings.{dev,test,production}`, all built on `config.settings.base`.
Copy `.env.example` to `.env` and adjust before running outside the Docker Compose defaults. Production settings
hard-fail (`ImproperlyConfigured`) if `SECRET_KEY`/`ALLOWED_HOSTS` are missing and hardcode `DEBUG=False`
regardless of environment — see `docs/architecture/08-nonfunctional-plan.md`.
