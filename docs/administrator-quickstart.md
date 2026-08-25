# Administrator Quick-Start Guide

Everything in the [Stock Manager guide](stock-manager-quickstart.md) applies to you too — Administrators see and
do everything a Stock Manager can, everywhere, plus the following.

## Creating a new user and assigning their role

There's no "add user" button inside the app itself — that's handled by Django's own built-in admin site at
`/admin/`, which is where new `User` accounts get created and put into one of the three role groups
(**Administrator**, **StockManager**, **ReadOnlyUser** — a user should normally be in exactly one). The very first
Administrator account is created automatically the first time the app starts — username `admin`, password `admin`
— and you're forced to change that password before anything else in the app is reachable
([`deploy/DEPLOYMENT.md`](../deploy/DEPLOYMENT.md)'s "Default admin account" section). Any Administrator can reach
`/admin/` (being in the Administrator group grants that automatically) to create the next one.

Steps: `/admin/` → **Users** → **Add user** → set a username and temporary password → save → open the new user →
add them to the right group under **Permissions** → save again. They should change their password after first
login (**Password** link on their own account, or the login screen's password-change flow).

## Granting location access

A new Stock Manager or Read-Only user can't see anything until you grant them access to specific storage locations
(**Settings → Manage access** in the nav). Access to a location automatically includes everything under it in the hierarchy
(Country → Site → Floor → Storage Room → Rack/Cabinet → Shelf/Bin) — granting "Room A" also grants every rack and
shelf inside Room A. Revoking access is immediate. Every grant and revoke is itself an audited event.

## Managing locations

**Locations** — create the Country/Site/Floor/Storage Room/Rack/Shelf tree your business actually uses (or run
`manage.py seed_locations` for a sample tree in a fresh dev environment — not for production). A location can be
deactivated (hidden from new receipts/transfers) without losing its history — nothing is ever hard-deleted.

## Product catalog

Products, Brands, and Types are created inline as you receive stock, or managed directly under **Products**. Once
a product has any movement against it, its tracking method (unit-serialized vs. quantity) locks — this is
deliberate, since converting live stock between the two isn't a safe automatic operation.

## Customizing the sign-off/delivery document template

**Settings → Document templates** — edit the printable PDF generated for assignment and delivery transactions (the form a
customer or employee physically signs when stock leaves): layout, wording, and your company logo, without needing
a code change. Start from the packaged default already loaded in the editor, use the documented field list on the
same screen to pull in real data (product/serial details, customer name, dates, etc.), and:

- **Preview** as often as you like — it opens a real PDF rendered from your in-progress edit against sample data in
  a new tab, before you save anything.
- **Save** re-validates your template against that same sample data first — a broken template is rejected with the
  error shown right there, and never overwrites your last working version.
- **Reset to packaged default** if you want to start over.

This only affects the Assignment/Delivery PDF — the Reports section's own screens are unaffected.

## System configuration and TLS certificate

**Settings → System configuration** — set the site name and logo shown in the sidebar and browser tab, and
optionally tighten `ALLOWED_HOSTS` from the browser instead of editing `.env.production` and restarting — takes
effect on the very next request. Leave the hosts field blank to keep the deployment's configured default (a
wildcard unless you've already set one). Get the hostname wrong and you'll lock yourself out of the site; recover
by connecting to the server and clearing it directly (`deploy/DEPLOYMENT.md`'s "Hostnames" section has the exact
command).

**Settings → TLS certificate** — upload a real `fullchain.pem`/`privkey.pem` pair to replace the temporary
self-signed certificate `install.sh` generates, without needing to get the files onto the server yourself first.
One manual step still remains after uploading: restart the reverse proxy container so it picks up the change
(`deploy/DEPLOYMENT.md`'s "Certificates" section has the exact command) — uploading doesn't do this automatically.

## Corrections and reversals

From an asset, balance, or transaction's detail page, an Administrator-only **Correct** or **Reverse** action is
available. A correction/reversal never edits or deletes the original record — it adds a new entry that fixes the
current state while leaving the original exactly as it was, so the full history (including the mistake) stays
visible.

## Importing the legacy Excel workbook

**Excel Import** — upload a `.xlsx` or `.csv` file matching the legacy column layout (download the template from
the same screen if you're building one from scratch rather than exporting from the old system). Every row is
staged and validated before anything is written — you'll see exactly what will happen row by row, fix any
unresolved locations by picking the right one, and only then execute. Rows with an unresolved location or other
issue are skipped, not guessed at; re-running the import after fixing them only touches what's still outstanding,
so retrying is always safe. Every row becomes an initial stock receipt — the legacy file's delivery/return/removal
columns are preserved as notes on the resulting record, not auto-converted into further movements; enter those
manually afterward if needed.

## Scheduled Excel export (backup safety net)

**Settings → Scheduled export** — point a local or network path at where you want a full Excel snapshot of current inventory
written nightly or weekly, as a human-readable safety net alongside the database-level backup your deployment runs
(`deploy/backup.sh`/`deploy/RESTORE.md`). Use **Run export now** to confirm the path actually works before relying
on the schedule. If a scheduled run fails (e.g. a disconnected network share), you'll see it on this screen and in
the audit log — check it periodically, or wire cron's own failure notification (`deploy/DEPLOYMENT.md`).

## Audit log

**Audit Log** — every login, record change, movement, duplicate acknowledgement, correction, permission change,
document generation, and import/export is recorded here and can never be edited or deleted by anyone, including
Administrators. Filter by event type, actor, or date to investigate a specific question.

## Reports

**Reports** covers every report in the spec — current stock, stock by location, reserved stock, employee
assignments, customer deliveries, stock by project reference, temporary assignments, damaged/lost/disposed assets
(with a dedicated view for disposed HDDs), movement history, and low stock (only shown for products you've set a
threshold on — nothing alerts automatically unless you've configured it).

## Deployment, backup, and security operations

Day-to-day application administration is everything above; server/infrastructure administration (production
deployment, database backup and restore, the login-throttling and error-page behavior, the optional database
role hardening) is covered in [`deploy/DEPLOYMENT.md`](../deploy/DEPLOYMENT.md) and
[`docs/architecture/08-nonfunctional-plan.md`](architecture/08-nonfunctional-plan.md).
