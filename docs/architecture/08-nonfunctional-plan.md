# Security, Backup, Testing, Observability, Performance

Covers spec §12, §17, Prompt 1/8.

## Security

- **Auth**: Django's built-in auth, PBKDF2/Argon2 (whichever is Django's current default at implementation time —
  no custom hasher). Login throttling via `django-axes` or an equivalent small, maintained package (evaluated in
  Phase 1; avoid hand-rolled lockout logic).
- **Sessions/CSRF**: Django's CSRF middleware everywhere (including HTMX requests — HTMX sends the CSRF header via
  the standard Django template pattern), `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`=true in production,
  configurable session timeout (env var, default conservative e.g. 8 hours idle).
- **Password policy**: Django's built-in validators (`MinimumLengthValidator`, `CommonPasswordValidator`,
  `NumericPasswordValidator`, `UserAttributeSimilarityValidator`) at minimum; exact minimum length is a deferred
  decision (spec §24) — defaulted to 12 characters, overridable via settings before launch.
- **Authorization**: every read/write/report/export/download goes through `core.scoping` (doc 04) — no exceptions,
  enforced by tests that specifically probe cross-scope object access on every detail/download view.
- **Uploads**: extension + sniffed-content-type allow-list, size cap, storage-name derived from a UUID (never the
  client filename), files stored outside any publicly served path (doc 06).
- **Error handling**: `DEBUG=False` by default in the production settings module (not just an env var default — the
  production settings file itself hardcodes `DEBUG=False` and requires `ALLOWED_HOSTS`/`SECRET_KEY` from the
  environment, so misconfiguration fails closed); custom 500/403/404 pages that never leak a traceback.
- **Secrets**: environment variables / Docker secrets only; `.env.example` committed with no real values; a startup
  check fails fast if a required production secret is missing rather than silently running with an insecure
  default.
- **DB-level defense in depth**: `AuditEvent` and completed `InventoryTransaction` rows have their `UPDATE`/`DELETE`
  grants revoked from the application's Postgres role (doc 02) — authorization bugs in application code cannot
  rewrite history even in the worst case.

## Backup and restore

- **Database**: nightly `pg_dump` (custom format) via a scheduled job in the Docker Compose stack (a small
  `cron`-based sidecar container, or the host's own cron calling `docker compose exec`), retained on a rolling
  window (e.g. 14 daily + 8 weekly — exact retention is an operational decision, not a spec requirement, defaulted
  and documented as adjustable).
- **Attachments/generated documents**: the protected volume is backed up alongside the database dump (same
  schedule) since `GeneratedDocument`/`Attachment` rows reference files on disk, not blobs in Postgres — a DB-only
  backup without the volume is incomplete.
- **Restore procedure**: documented step-by-step in `deploy/RESTORE.md` (created in Phase 4/Prompt 8): restore the
  volume, `pg_restore` into a fresh database, run `manage.py migrate --check`, verify a smoke-test query. Verified
  at least once in a disposable environment before production launch, per spec §17 explicit requirement — this is
  a required gate, not optional documentation.

## Testing

- `pytest` + `pytest-django` for unit/service tests — one test module per service file in each app, with explicit
  coverage of every status transition, every scope-violation scenario, duplicate-serial/product acknowledgement,
  quantity integrity (no negative balance except audited correction), partial returns, and correction/reversal, per
  spec §23.4 and each phase's prompt.
- Browser-level tests (Playwright via `pytest-playwright`, or Django's `LiveServerTestCase` + Selenium — decided in
  Phase 1 based on what's easiest to run in the Docker dev environment) for the critical multi-step workflows:
  receive → reserve → assign → partial return, and the import preview/execute flow.
- Query-count tests (`django.test.utils.CaptureQueriesContext` or `django-assert-num-queries`) on list/report views
  to catch N+1 regressions before they hit production data volumes (spec §17 explicit requirement).
- A seeded 8,000+-row dataset (via a management command) used both for functional tests at realistic scale and for
  measuring the sub-1-second filtered-list target — measured and reported honestly per phase, never asserted
  without having actually run it (explicit instruction carried from the prompt pack into every phase).

## Observability

- Structured logging (JSON in production, human-readable in dev) via Django's logging framework — request IDs,
  authenticated user, and enough context to trace a movement from request to `AuditEvent` row without exposing
  secrets or full request bodies.
- Docker health/readiness endpoint (`core` app) checked by Compose `healthcheck:` — DB connectivity is not on the
  application's user-facing request path.
- No external APM/metrics service is introduced initially — out of scope per §3 ("avoids unnecessary frontend/API
  complexity") until a demonstrated need exists, consistent with the "don't add Celery/Redis without a demonstrated
  requirement" instruction repeated throughout the prompt pack.

## Performance

- All list/report/search queries go through `core.scoping` (indexed `ltree` filter) plus indexed filter fields
  listed per-entity in doc 02 (serial, normalized Brand/Model/SKU, Project Reference, Final Customer, status,
  location, movement dates) — matching spec §17's explicit index list.
- Server-side pagination everywhere (Django's `Paginator`, cursor-based for very large result sets if offset
  pagination proves too slow at 100k+ rows — decided empirically in Phase 3/7 against real seeded data, not
  pre-optimized speculatively).
- `select_related`/`prefetch_related` required on every list/report queryset touching FKs rendered per row
  (Brand/Product/Location names, etc.) — enforced by the query-count tests above.
- Designed for 100,000+ inventory records without a redesign (spec §17) — the ledger-plus-denormalized-current-
  state pattern (doc 02) is what makes this achievable: list/filter queries never need to aggregate the full ledger
  at read time.
