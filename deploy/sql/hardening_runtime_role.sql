-- Defense-in-depth for the ledger/audit tables (docs/architecture/08-nonfunctional-plan.md,
-- docs/architecture/02-data-model.md's deletion policy summary): even a bug in application
-- code must not be able to UPDATE or DELETE an audit/ledger row.
--
-- The role Django migrates as (POSTGRES_USER) OWNS every table, and PostgreSQL lets table
-- owners bypass GRANT/REVOKE — so hardening the owning role itself is a no-op. This script
-- creates a SEPARATE, lower-privilege role for the running application to connect as day to
-- day; migrations keep running as the owning role.
--
-- Usage: run once per environment, as a role with GRANT/REVOKE privileges (the owning role
-- or a superuser), AFTER `manage.py migrate` has created every table. Idempotent — safe to
-- re-run after new migrations add tables, which is required for those new tables to become
-- writable by the runtime role (see the ALTER DEFAULT PRIVILEGES lines below, and the manual
-- follow-up step noted at the bottom for any *new* append-only table).
--
-- Variables (substitute before running, e.g. via `psql -v app_password=... -f ...`):
--   :app_role      the runtime role name, e.g. stock_inventory_app
--   :app_password  its login password (matches RUNTIME_DB_PASSWORD in production's .env)
--   :db_name       the application database name

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_role') THEN
        EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L', :'app_role', :'app_password');
    ELSE
        EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_role', :'app_password');
    END IF;
END
$$;

GRANT CONNECT ON DATABASE :"db_name" TO :"app_role";
GRANT USAGE ON SCHEMA public TO :"app_role";

-- Broad baseline: the runtime role can read/write ordinary tables normally.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_role";

-- Narrow: revoke UPDATE/DELETE specifically on every append-only ledger/audit table
-- (apps.core.models.AppendOnlyModel subclasses — keep this list in sync with that set).
REVOKE UPDATE, DELETE ON audit_auditevent FROM :"app_role";
REVOKE UPDATE, DELETE ON inventory_inventorytransaction FROM :"app_role";
REVOKE UPDATE, DELETE ON inventory_inventorytransactionline FROM :"app_role";
REVOKE UPDATE, DELETE ON inventory_assetstatushistory FROM :"app_role";
REVOKE UPDATE, DELETE ON documents_generateddocument FROM :"app_role";

-- MANUAL FOLLOW-UP: a future migration that adds a new AppendOnlyModel subclass needs a new
-- REVOKE line added here (and this script re-run) — ALTER DEFAULT PRIVILEGES only grants the
-- broad baseline to new tables automatically, it cannot know which ones are meant to be
-- append-only. deploy/DEPLOYMENT.md's migration checklist calls this out explicitly.
