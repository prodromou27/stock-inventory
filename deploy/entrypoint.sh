#!/usr/bin/env bash
# Runs before the container's real command (CMD) on every start — makes
# `docker compose up` alone a complete, working install: migrations applied
# and a default Administrator ready to log in with, no separate `exec`
# steps required. Both are safe to repeat on every restart: `migrate` is
# idempotent by nature, and `bootstrap_admin` (docs/architecture/04-permission-matrix.md's
# "Default admin bootstrap" section) does nothing once any Administrator
# exists — it can never reset an operator's already-changed password.
set -euo pipefail

echo "entrypoint: waiting for the database and applying migrations..."
python manage.py migrate --noinput

echo "entrypoint: bootstrapping the default Administrator (no-op if one already exists)..."
python manage.py bootstrap_admin

echo "entrypoint: starting the app..."
exec "$@"
