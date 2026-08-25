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

Installation is one command:

```
git clone https://github.com/prodromou27/stock-inventory.git
cd stock-inventory
./deploy/install.sh
```

`deploy/install.sh` generates everything a first run needs and nothing more:

- `.env.production` (gitignored) with a random `SECRET_KEY`, `POSTGRES_PASSWORD`, and `BOOTSTRAP_ADMIN_PASSWORD` —
  no secret is ever typed or copy-pasted by an operator. `ALLOWED_HOSTS` is left wildcarded (see below).
- A temporary self-signed TLS certificate under `deploy/certs/`, if none is already there, so HTTPS works
  immediately — replace it with a real one whenever you have it (see "Certificates" below); nothing else about the
  install needs to wait for that.
- A symlink at `deploy/.env` pointing to `../.env.production` — Compose auto-loads a file literally named `.env`
  next to the compose file for its own `${VAR}` substitution (used by the `db` service's `POSTGRES_PASSWORD`,
  deliberately with no hardcoded fallback — a production DB password must never have one). This is a *different*
  mechanism from `web`'s `env_file: ../.env.production`, which only injects vars into that one container. Without
  this symlink, any `docker compose -f deploy/docker-compose.prod.yml ...` command run without an explicit
  `--env-file .env.production` — which every command in this file *except* `install.sh` itself would otherwise be
  — resolves `POSTGRES_PASSWORD` to empty and `db` refuses to start. (Found for real on a first deployment attempt,
  not merely anticipated — see the verification note above.) Every command below relies on this symlink already
  existing, so run `./deploy/install.sh` at least once before any of them.
- Then runs `docker compose --env-file .env.production -f deploy/docker-compose.prod.yml up -d --build` (the
  `--env-file` flag here is belt-and-suspenders, redundant with the symlink above but harmless), which triggers
  `deploy/entrypoint.sh` inside `web`: `manage.py migrate` and `manage.py bootstrap_admin`, before the app starts
  serving.

At the end it prints the generated Administrator password once. Log in at `https://<this-host>/` with username
`admin` — the app **blocks every other page** until you change that password; there is no way to skip this. Do it
immediately, especially before the instance is reachable from an untrusted network.

**Re-running `./deploy/install.sh` is always safe** — an existing `.env.production` or certificate is reused, never
regenerated (regenerating `POSTGRES_PASSWORD` or `SECRET_KEY` in place would break an already-running install), so
this is also the command for every later deploy (see "Routine deployment" below).

### Hostnames (`ALLOWED_HOSTS`)

Left wildcarded (`*`) by default — a fresh install doesn't need a hostname decided up front, and this alone does
not weaken HTTPS, CSRF, or session cookie security (those are enforced independently). Tighten it later by editing
`ALLOWED_HOSTS` in `.env.production` to a comma-separated list of your real hostname(s) and restarting `web`
(`docker compose -f deploy/docker-compose.prod.yml up -d web`) — there is no in-app settings screen for this yet
(it's read once at process start, before any request — including the very first one — can be handled), so an env
edit + restart is the only path today.

### Certificates

`install.sh` generates a temporary self-signed certificate so the single command produces a working HTTPS site
immediately, with the trade-off that browsers will show a trust warning until it's replaced. Swap in a real one at
any time — no rebuild needed:

```
cp your-fullchain.pem deploy/certs/fullchain.pem
cp your-privkey.pem deploy/certs/privkey.pem
docker compose -f deploy/docker-compose.prod.yml restart proxy
```

There is no self-service upload-a-certificate screen in the app yet — this remains a file-drop-and-restart
operation an operator performs on the host. (Uploading through an in-app settings screen would need new
infrastructure — a way for `web`, running in its own container, to hand a file to `proxy`'s container and trigger
an nginx reload — that hasn't been built; flagged here rather than implied to already work.)

### Optional: hardened DB runtime role

Defense in depth so a bug in application code cannot `UPDATE`/`DELETE` an audit/ledger row (doc 08; the
migration-owning role can bypass `GRANT`/`REVOKE` on tables it owns, so this requires a genuinely separate role).
Do this any time after the first `./deploy/install.sh` run (migrations already applied). `POSTGRES_USER`/
`POSTGRES_DB` below come from `.env.production`, not your shell — `set -a; source .env.production; set +a` first,
or substitute the values (`stock_inventory` by default) directly:

```
set -a; source .env.production; set +a
docker compose -f deploy/docker-compose.prod.yml exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v app_role=stock_inventory_app -v app_password='<a-real-generated-password>' -v db_name="$POSTGRES_DB" \
    -f /dev/stdin < deploy/sql/hardening_runtime_role.sql
```

Then set `RUNTIME_DB_USER`/`RUNTIME_DB_PASSWORD` in `.env.production` and restart `web` (`docker compose -f
deploy/docker-compose.prod.yml up -d web`) — from then on the running app connects as the restricted role;
`manage.py migrate` for future releases still needs to run as the owning role (entrypoint.sh always uses
`POSTGRES_USER`, never the runtime role, for exactly this reason), and **`hardening_runtime_role.sql` must be
re-run any time a migration adds a new append-only table** (the script's own trailing comment lists the current
set to keep in sync).

### Default admin account

`docs/architecture/04-permission-matrix.md`'s "Default admin bootstrap" section has the full design and the
security trade-off it makes explicit. In production, `BOOTSTRAP_ADMIN_PASSWORD` is required and cannot be `admin`
— `install.sh` always generates a real random one, and `bootstrap_admin` itself independently refuses to start
with a blank or literal `admin` password when `DJANGO_SETTINGS_MODULE=config.settings.production`, so this can't
regress even if `.env.production` is later hand-edited carelessly. `BOOTSTRAP_ADMIN_USERNAME` defaults to `admin`.
These values create exactly one Administrator, only if none already exists — safe to leave running forever, since
it can never reset a password an operator already changed. Set `BOOTSTRAP_ADMIN_ENABLED=false` in
`.env.production` to disable this entirely and fall back to a manual
`docker compose -f deploy/docker-compose.prod.yml exec web python manage.py createsuperuser` instead, if you'd
rather not have a predictable-username bootstrap account at all, even briefly.

## Routine deployment (a new release)

```
git pull
./deploy/install.sh
```

Reuses the existing `.env.production` and certificate untouched, rebuilds the image, and re-applies migrations
automatically (`deploy/entrypoint.sh`) before the new container starts serving traffic. If the release added a new
`AppendOnlyModel` subclass (check `git log` / the migration for a new ledger/audit-style table), re-run the
hardening script above before or immediately after this deploy.

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

## Scheduled Excel export

Separate from the database backup above: an Administrator can configure a local or network path (**Export
Settings** in the app nav, Administrator-only) that a full Excel snapshot of unit assets and stock balances gets
written to on a nightly or weekly schedule — a human-readable safety net a non-technical user can inspect directly,
without needing `pg_restore`. The path must already be reachable from wherever the app runs (mount/share it into
the container first if it's a network path); the settings screen validates this by writing a test file when you
save. Like `backup.sh`, this doesn't self-schedule — invoke it daily via cron and let it decide internally whether
today is a run day:

```
0 3 * * * docker compose -f /path/to/deploy/docker-compose.prod.yml exec -T web \
    python manage.py run_scheduled_export >> /var/log/stock-inventory-export.log 2>&1
```

The last run's outcome (including the failure reason, if any — e.g. a disconnected network share) is shown on the
settings screen and recorded as an `AuditEvent`; the command also exits non-zero on failure so cron's own mail-on
-error or your monitoring can catch it independently.

## Restore

See [`RESTORE.md`](RESTORE.md) — practice it in a disposable environment before you need it for real.

## Monitoring

No external APM/metrics service by default (doc 08, spec §3 — avoid unnecessary complexity until a demonstrated
need exists). `web`'s Docker healthcheck and `/healthz/` are what a host-level monitor (cron, an uptime checker,
your platform's own health-check feature) should poll. Logs are structured JSON on stdout
(`config/settings/production.py`) — point your log collector (if any) at the container's stdout rather than a
file inside it.
