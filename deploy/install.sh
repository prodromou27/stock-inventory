#!/usr/bin/env bash
# The one command: `./deploy/install.sh`. First run generates every secret
# (.env.production) and a bootstrap self-signed TLS certificate if neither
# already exists, then brings the whole production stack up. Safe to re-run
# for later deploys — an existing .env.production/certificate is reused,
# never regenerated (regenerating POSTGRES_PASSWORD or SECRET_KEY in place
# would break an already-running install), so this doubles as the "routine
# deployment" command too (see deploy/DEPLOYMENT.md).
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE_FILE="deploy/docker-compose.prod.yml"
ENV_FILE=".env.production"
CERTS_DIR="deploy/certs"

_random_hex() {
    local bytes="$1"
    local value=""
    if command -v openssl >/dev/null 2>&1; then
        value=$(openssl rand -hex "$bytes")
    elif [ -r /dev/urandom ]; then
        value=$(head -c "$bytes" /dev/urandom | od -An -tx1 | tr -d ' \n')
    elif command -v python3 >/dev/null 2>&1; then
        value=$(python3 -c "import secrets; print(secrets.token_hex($bytes))")
    fi
    # Every generator above can in principle exit 0 with no output (a flaky
    # openssl, a stripped-down image with a broken od/head). A blank secret
    # written to $ENV_FILE would otherwise go unnoticed forever, since a
    # second run treats an existing $ENV_FILE as already-generated and never
    # looks at its contents again — so fail loudly here instead.
    if [ -z "$value" ]; then
        echo "Failed to generate a secure random value (need a working openssl, /dev/urandom, or python3)." >&2
        exit 1
    fi
    echo "$value"
}

# Guards against a $ENV_FILE left over from a prior run that generated the
# file but wrote a blank value for one of these (e.g. a transient generator
# failure) — reusing it silently, forever, is exactly what let that go
# unnoticed until db refused to start on a real deployment. Heals in place
# rather than failing, so a re-run of this script is still the one command
# that gets an already-broken install running.
_ensure_secret_set() {
    local key="$1" bytes="$2"
    local current
    current=$(grep "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2-)
    if [ -z "$current" ]; then
        echo "$key in $ENV_FILE is empty (likely a prior run's generator failed partway through) —" >&2
        echo "regenerating it now." >&2
        local value
        value=$(_random_hex "$bytes")
        if grep -q "^${key}=" "$ENV_FILE"; then
            sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
        else
            echo "${key}=${value}" >> "$ENV_FILE"
        fi
    fi
}

GENERATED_NEW_ENV=false
if [ -f "$ENV_FILE" ]; then
    echo "$ENV_FILE already exists — reusing it (secrets are never regenerated over an existing install)."
    _ensure_secret_set SECRET_KEY 32
    _ensure_secret_set POSTGRES_PASSWORD 16
    _ensure_secret_set BOOTSTRAP_ADMIN_PASSWORD 12
else
    echo "Generating $ENV_FILE with fresh, random secrets..."
    SECRET_KEY=$(_random_hex 32)
    POSTGRES_PASSWORD=$(_random_hex 16)
    BOOTSTRAP_ADMIN_PASSWORD=$(_random_hex 12)

    cat > "$ENV_FILE" <<EOF
DJANGO_SETTINGS_MODULE=config.settings.production

SECRET_KEY=$SECRET_KEY

# Wildcarded for a zero-config first deploy (config/settings/production.py
# defaults to this same value if unset) — tighten to your real hostname(s)
# whenever convenient; nothing else depends on it being open.
ALLOWED_HOSTS=*
CSRF_TRUSTED_ORIGINS=

BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=$BOOTSTRAP_ADMIN_PASSWORD

POSTGRES_DB=stock_inventory
POSTGRES_USER=stock_inventory
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_PORT=5432

# Optional DB-role hardening (deploy/sql/hardening_runtime_role.sql) — leave
# blank to skip it; see deploy/DEPLOYMENT.md.
RUNTIME_DB_USER=
RUNTIME_DB_PASSWORD=

SESSION_COOKIE_AGE=28800
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=3600
SECURE_HSTS_PRELOAD=False

AXES_FAILURE_LIMIT=5
AXES_COOLOFF_MINUTES=30

LOG_LEVEL=INFO
DJANGO_LOG_LEVEL=INFO
EOF
    chmod 600 "$ENV_FILE"
    GENERATED_NEW_ENV=true
fi

# Docker Compose auto-loads a file literally named ".env" next to the compose
# file for its own ${VAR} substitution (docker-compose.prod.yml's `db` service
# environment block, deliberately with no hardcoded fallback for
# POSTGRES_PASSWORD — a production DB password must never have one) — that's
# a *different* mechanism from `web`'s `env_file: ../.env.production`, which
# only injects vars into that one container. Without this symlink, any
# `docker compose -f deploy/docker-compose.prod.yml ...` command run without
# an explicit `--env-file .env.production` (which is easy to forget, and this
# script itself only covers the commands below) resolves POSTGRES_PASSWORD to
# empty and `db` refuses to start.
ln -sf "../$ENV_FILE" "deploy/.env"

mkdir -p "$CERTS_DIR"
# Settings > TLS certificate (apps.settings.services.update_certificate)
# writes here from inside web's container as its non-root `app` user, on
# top of proxy's existing read-only mount of this same host directory
# (deploy/docker-compose.prod.yml) — world-writable on the directory (not
# the files) is the simplest way to make that work across arbitrary host
# UID/GID setups without needing to know web's container UID ahead of
# time; the private key file itself is still written with mode 600 by
# that service regardless.
chmod 777 "$CERTS_DIR"

if [ -f "$CERTS_DIR/fullchain.pem" ] && [ -f "$CERTS_DIR/privkey.pem" ]; then
    echo "Existing TLS certificate found under $CERTS_DIR — reusing it."
else
    echo "No TLS certificate found — generating a temporary self-signed one so HTTPS works immediately."
    echo "Browsers will show a trust warning until you replace it with a real certificate (see below)."
    if ! command -v openssl >/dev/null 2>&1; then
        echo "openssl is required to generate a self-signed certificate. Install it, or place a real" >&2
        echo "certificate at $CERTS_DIR/fullchain.pem and $CERTS_DIR/privkey.pem yourself, then re-run." >&2
        exit 1
    fi
    openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
        -keyout "$CERTS_DIR/privkey.pem" -out "$CERTS_DIR/fullchain.pem" \
        -subj "/CN=stock-inventory.local" >/dev/null 2>&1
fi

echo "Starting the stack (this also applies migrations and bootstraps the Administrator — deploy/entrypoint.sh)..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build

echo "Waiting for the app to report healthy..."
for _ in $(seq 1 60); do
    status=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps web --format json 2>/dev/null \
        | grep -o '"Health":"[a-z]*"' | head -1 | cut -d'"' -f4 || true)
    if [ "$status" = "healthy" ]; then
        break
    fi
    sleep 2
done

echo ""
echo "================================================================"
echo "Deployment complete."
echo ""
if [ "$GENERATED_NEW_ENV" = true ]; then
    echo "Log in at https://<this-host>/ with:"
    echo "  username: admin"
    echo "  password: $BOOTSTRAP_ADMIN_PASSWORD"
    echo ""
    echo "You will be forced to change this password before anything else in the"
    echo "app is reachable. These credentials are also saved in $ENV_FILE (mode 600)."
else
    echo "Reused an existing $ENV_FILE — see BOOTSTRAP_ADMIN_USERNAME/PASSWORD in that"
    echo "file if this is the first time this install has actually started."
fi
echo ""
echo "The certificate serving HTTPS right now may be a temporary self-signed one."
echo "Replace $CERTS_DIR/fullchain.pem and privkey.pem with a real certificate whenever"
echo "you have one, then run: docker compose -f $COMPOSE_FILE restart proxy"
echo "================================================================"
