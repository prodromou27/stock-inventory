# Restore procedure

Restores a database backup produced by `deploy/backup.sh` (see
[`docs/architecture/08-nonfunctional-plan.md`](../docs/architecture/08-nonfunctional-plan.md)). Practice this in a
disposable environment before you ever need it for real — spec §17 requires the procedure to be verified, not just
written down (see "Verified" at the bottom of this file for the record of the last such run).

## 1. Restore the media volume

If restoring alongside a full disaster recovery (not just a database rollback), extract the media tarball to the
`media` volume/directory before starting the app:

```
tar -xzf media_<timestamp>.tar.gz -C /path/to/media/parent/
```

## 2. Restore the database into a fresh database

Never restore over a live database in place — create a new one, verify it, then cut over.

```
createdb --host=$POSTGRES_HOST --port=$POSTGRES_PORT --username=$POSTGRES_USER --owner=$POSTGRES_USER restore_check
pg_restore --host=$POSTGRES_HOST --port=$POSTGRES_PORT --username=$POSTGRES_USER \
    --dbname=restore_check --no-owner --no-privileges db_<timestamp>.dump
```

## 3. Verify schema and data before cutting over

```
DJANGO_SETTINGS_MODULE=config.settings.dev POSTGRES_DB=restore_check python manage.py migrate --check
```

Expect no output and exit code 0 — any pending migration means the dump is from an older schema version than the
code you're about to run against it, which needs resolving before cutover, not after.

Then run a smoke-test query confirming real data came back (row counts should roughly match what you expect from
the environment being restored):

```
DJANGO_SETTINGS_MODULE=config.settings.dev POSTGRES_DB=restore_check python manage.py shell -c "
from django.contrib.auth import get_user_model
from apps.inventory.models import UnitAsset, InventoryTransaction
from apps.audit.models import AuditEvent
User = get_user_model()
print('users:', User.objects.count())
print('unit assets:', UnitAsset.objects.count())
print('transactions:', InventoryTransaction.objects.count())
print('audit events:', AuditEvent.objects.count())
"
```

## 4. Cut over

Once step 3 looks right: stop the application, rename/drop the old database, rename `restore_check` to the
production database name (or repoint `POSTGRES_DB` at it), then start the application again. If the runtime role
hardening (`deploy/sql/hardening_runtime_role.sql`) is in use, re-run that script against the restored database
before starting the app — role grants are not part of a `pg_dump`/`pg_restore` of a single database's data, only
its own objects, so the runtime role's cross-database `GRANT`s need reapplying.

## 5. Clean up

```
dropdb --host=$POSTGRES_HOST --port=$POSTGRES_PORT --username=$POSTGRES_USER restore_check
```

(Skip this step if `restore_check` is what you just cut over to production.)

## Verified

- **2026-08-25**, against the local dev database (real seeded data — 8 users, 20 locations, 26 unit assets, 44
  transactions, 144 audit events): `deploy/backup.sh` produced a valid `pg_dump` (custom format) and a media
  tarball; the dump was `pg_restore`d into a fresh disposable database (`restore_test_db`); `manage.py migrate
  --check` reported no pending migrations against the restored schema; a smoke-test query confirmed all row counts
  matched the source database exactly. The disposable database was then dropped. (Run without Docker — this
  machine doesn't have it installed — directly against a local PostgreSQL 16 instance; the commands are identical
  inside a container, only the host/port differ.)
