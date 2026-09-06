#!/usr/bin/env bash
# Nightly backup: a pg_dump (custom format, so pg_restore can do a
# selective/parallel restore) plus a tarball of the media volume
# (attachments/generated documents referenced by Attachment/GeneratedDocument
# rows are files on disk, not blobs in Postgres — a DB-only backup is
# incomplete). See docs/architecture/08-nonfunctional-plan.md and
# deploy/RESTORE.md.
#
# Run from inside the `web` container (or anywhere with pg_dump and network
# access to `db`, and the media volume mounted), e.g. via a cron sidecar:
#   docker compose -f deploy/docker-compose.prod.yml exec -T web deploy/backup.sh
#
# Required env: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER,
# POSTGRES_PASSWORD (same variables the app itself already uses).
# Optional env: BACKUP_DIR (default ./backups), BACKUP_RETENTION_DAYS
# (default 14 — older backups are deleted after a successful new backup).

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MEDIA_DIR="${MEDIA_DIR:-./media}"

mkdir -p "$BACKUP_DIR"

DB_DUMP="$BACKUP_DIR/db_${TIMESTAMP}.dump"
MEDIA_TAR="$BACKUP_DIR/media_${TIMESTAMP}.tar.gz"

echo "Backing up database to $DB_DUMP ..."
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
    --host="$POSTGRES_HOST" --port="${POSTGRES_PORT:-5432}" \
    --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
    --format=custom --file="$DB_DUMP"

echo "Backing up media volume to $MEDIA_TAR ..."
if [ -d "$MEDIA_DIR" ]; then
    # --force-local: without it, GNU tar treats a "C:/..." path as a
    # user@host:path remote spec because of the drive-letter colon — only
    # matters when this runs on Windows (e.g. local testing outside the
    # Linux container), but harmless everywhere else.
    tar --force-local -czf "$MEDIA_TAR" -C "$(dirname "$MEDIA_DIR")" "$(basename "$MEDIA_DIR")"
else
    echo "Warning: $MEDIA_DIR does not exist yet (no attachments/documents generated); skipping." >&2
fi

echo "Pruning backups older than ${RETENTION_DAYS} days ..."
find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'db_*.dump' -o -name 'media_*.tar.gz' \) \
    -mtime "+${RETENTION_DAYS}" -print -delete

echo "Backup complete: $DB_DUMP"
[ -f "$MEDIA_TAR" ] && echo "Backup complete: $MEDIA_TAR"
