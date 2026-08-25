# Production deployment runbook

Covers spec §12/§17 and [`docs/architecture/08-nonfunctional-plan.md`](../docs/architecture/08-nonfunctional-plan.md).
For local development, use [`docker-compose.yml`](docker-compose.yml) and the root
[`CLAUDE.md`](../CLAUDE.md)/[`AGENTS.md`](../AGENTS.md) instead — this file is the production path.

**Verification note**: this runbook's compose topology (`docker-compose.prod.yml`, `Dockerfile.prod`, nginx) has
not been booted end to end via Docker in this repository — the machine this was built on doesn't have Docker
installed. What *has* been verified for real, directly against a local PostgreSQL 16 instance, without Docker: the
production settings module (`manage.py check --deploy`, `collectstatic` with the WhiteNoise compressed-manifest
backend, and a full request/response cycle including the custom error pages, all under `DEBUG=False` production
settings — see the commit history for Prompt 8), the backup script, a full restore into a disposable database (see
[`RESTORE.md`](RESTORE.md)), and the runtime-role hardening SQL (see below). The Docker layer itself follows
standard, well-established patterns (gunicorn + WhiteNoise + nginx reverse proxy) but should be smoke-tested once
in a real Docker environment before the first production cutover.

## First-time setup

1. **Secrets**: copy [`../.env.production.example`](../.env.production.example) to `.env.production` (repo root,
   gitignored) and fill in `SECRET_KEY` (generate with
   `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`),
   `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and `POSTGRES_PASSWORD`.
2. **TLS**: place your certificate and key at `deploy/certs/fullchain.pem` / `deploy/certs/privkey.pem` (gitignored
   — see [`nginx.conf.example`](nginx.conf.example)'s header comment on obtaining one). Copy
   `nginx.conf.example` to `nginx.conf` and set `server_name` to your real hostname.
3. **Build and start the database first**:
   ```
   docker compose -f deploy/docker-compose.prod.yml up -d db
   ```
4. **Build the app image and run migrations** (as the owning role — `POSTGRES_USER`/`PASSWORD`, not the runtime
   role, since the runtime role doesn't exist yet and wouldn't have DDL privileges regardless):
   ```
   docker compose -f deploy/docker-compose.prod.yml build web
   docker compose -f deploy/docker-compose.prod.yml run --rm web python manage.py migrate
   ```
5. **(Recommended) Provision the hardened runtime role** — defense in depth so a bug in application code cannot
   `UPDATE`/`DELETE` an audit/ledger row (doc 08; the migration-owning role can bypass `GRANT`/`REVOKE` on tables
   it owns, so this requires a genuinely separate role):
   ```
   docker compose -f deploy/docker-compose.prod.yml exec db psql -U $POSTGRES_USER -d $POSTGRES_DB \
       -v app_role=stock_inventory_app -v app_password='<a-real-generated-password>' -v db_name=$POSTGRES_DB \
       -f /dev/stdin < deploy/sql/hardening_runtime_role.sql
   ```
   Then set `RUNTIME_DB_USER`/`RUNTIME_DB_PASSWORD` in `.env.production` to that role's credentials and restart
   `web` — from then on the running app connects as the restricted role; `manage.py migrate` for future releases
   still needs to run as the owning role (step 7 below), and **`hardening_runtime_role.sql` must be re-run any time
   a migration adds a new append-only table** (the script's own trailing comment lists the current set to keep in
   sync).
6. **Create the first Administrator**:
   ```
   docker compose -f deploy/docker-compose.prod.yml run --rm web python manage.py createsuperuser
   ```
7. **Start everything**:
   ```
   docker compose -f deploy/docker-compose.prod.yml up -d
   ```
   Confirm `web`'s healthcheck passes (`docker compose -f deploy/docker-compose.prod.yml ps`) and
   `https://<your-host>/healthz/` returns `{"status": "ok", "database": "ok"}` through the proxy.

## Routine deployment (a new release)

```
git pull
docker compose -f deploy/docker-compose.prod.yml build web
docker compose -f deploy/docker-compose.prod.yml run --rm web python manage.py migrate
docker compose -f deploy/docker-compose.prod.yml up -d web
```

If the release added a new `AppendOnlyModel` subclass (check `git log` / the migration for a new ledger/audit-style
table), re-run the hardening script (step 5 above) before or immediately after this deploy.

## Backups

Schedule [`backup.sh`](backup.sh) via host cron (or a small cron sidecar container) — it is not run automatically
by the compose file:

```
0 2 * * * docker compose -f /path/to/deploy/docker-compose.prod.yml exec -T -e BACKUP_DIR=/backups web \
    deploy/backup.sh >> /var/log/stock-inventory-backup.log 2>&1
```

Mount a host directory or separate volume at `/backups` in the `web` service if you want backups to survive the
container being recreated (not wired into `docker-compose.prod.yml` by default, since where backups should land is
an operational/storage decision, not one this file should presume). Retention defaults to 14 days
(`BACKUP_RETENTION_DAYS`).

## Restore

See [`RESTORE.md`](RESTORE.md) — practice it in a disposable environment before you need it for real.

## Monitoring

No external APM/metrics service by default (doc 08, spec §3 — avoid unnecessary complexity until a demonstrated
need exists). `web`'s Docker healthcheck and `/healthz/` are what a host-level monitor (cron, an uptime checker,
your platform's own health-check feature) should poll. Logs are structured JSON on stdout
(`config/settings/production.py`) — point your log collector (if any) at the container's stdout rather than a
file inside it.
