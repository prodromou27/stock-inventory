# Permission Matrix and Enforcement Strategy

Covers spec §4, Prompt 2.

## Two independent dimensions

Every authorization check is the AND of two independent checks:

1. **Role** — what kinds of actions the user's account can perform at all, from Django `Group` membership:
   `Administrator`, `StockManager`, `ReadOnlyUser` (exactly one per user; enforced by a form/admin validation, not a
   DB constraint, since Django doesn't make "exactly one group" trivial to express as a constraint).
2. **Location scope** — which `Location` subtrees the user may see/act on, from `UserLocationAccess` rows (doc 02),
   except Administrators, who need no grant rows at all — `is_administrator(user)` alone grants every location.

A request is authorized only if the role permits the *type* of action **and** every `Location` touched by the
request is within the user's granted scope (self or a descendant, via the `ltree` `path` column).

## Role × action matrix

| Action | Administrator | Stock Manager | Read-only User |
|---|---|---|---|
| View inventory/reports/history within scope | ✅ | ✅ | ✅ |
| Free-text search, filters, exports within scope | ✅ | ✅ | ✅ |
| Receive stock | ✅ | ✅ | ❌ |
| Reserve / release reservation | ✅ | ✅ | ❌ |
| Bulk location transfer | ✅ | ✅ | ❌ |
| Employee assignment | ✅ | ✅ | ❌ |
| Customer delivery | ✅ | ✅ | ❌ |
| Return / return assessment | ✅ | ✅ | ❌ |
| Mark damaged / lost / dispose | ✅ | ✅ | ❌ |
| Create/edit Product, Brand, ProductType | ✅ | ✅ (within scope) | ❌ |
| Upload transaction attachment | ✅ | ✅ | ❌ |
| Generate printable document | ✅ | ✅ | ❌ |
| Download attachment/document | ✅ | ✅ (within scope) | ✅ (within scope, view-only) |
| Run Excel import | ✅ | ✅ (within scope) | ❌ |
| Administrator correction / reversal | ✅ | ❌ | ❌ |
| Manage users, roles, `UserLocationAccess` | ✅ | ❌ | ❌ |
| Manage Country/Site/Floor/StorageRoom/Rack/Shelf (create/deactivate) | ✅ | ❌ | ❌ |
| View audit log | ✅ | ❌ | ❌ |
| Configure low-stock thresholds | ✅ | ❌ | ❌ |

All ✅ cells are additionally gated by location scope except the two admin-only management rows (user/role
management and audit log are global, not location-scoped, since they're about the system itself, not inventory).

## Enforcement: one scope layer, called everywhere

Role checks and location-scope checks are split across two small modules (see doc 01's dependency-table note for
why this ended up as two modules instead of one `core.scoping`):

`apps.core.authorization` provides:
- `is_administrator(user)`, `has_role(user, *group_names)`, `require_role(user, *group_names)` — raises
  `PermissionDenied`.
- `RoleRequiredMixin` — class-based-view mixin reading `allowed_roles`.

`apps.locations.scoping` provides:
- `accessible_locations(user) -> QuerySet[Location]` — every `Location` in the user's granted subtrees (or all, for
  Administrators, per `is_administrator`).
- `scope_queryset(user, queryset, location_field=None)` — wraps any queryset (inventory, transactions, reports)
  with one `path <@ <granted path>` clause per grant, OR'd together, using the custom `descendant_or_self` ORM
  lookup registered on `LtreeField` (`apps/locations/fields.py`) — a handful of grants per user means a handful of
  OR'd clauses, still a single indexed query, not a per-row Python check. (The original plan described this as a
  single `<@ ANY(accessible_paths)` array-operator query; the OR'd-clauses form was simpler to express through the
  Django ORM and is equivalent at the scale this app targets.)
- `require_location_access(user, location)` — raises `PermissionDenied` for use before any write; compares the
  target location's already-loaded `path` string against granted paths in Python (dot-prefix check), avoiding an
  extra query when the location object is already in hand.

**Every** list view, detail view, report, export, and attachment/document download calls `scope_queryset` or
`require_location_access` — there is no view that queries `UnitAsset`/`StockBalance`/`InventoryTransaction`/
`Attachment` directly without going through this layer, so a direct-URL object lookup (`/assets/<uuid>/`) is scoped
identically to a list page (spec §4, acceptance criterion §21.3). This is enforced structurally by code review /
a lint check (a `get_object_or_404(UnitAsset, ...)` outside the scoping layer is a review-blocking pattern), and by
tests that specifically attempt direct-URL access outside a user's scope for every object-detail and download view
(Prompt 2 required test list).

Navigation/UI hides actions the user's role/scope doesn't allow, but this is presentation only — every one of those
actions is independently rejected server-side if attempted directly, per spec §4 ("permission checks must be
performed on the server for every request") and Prompt 2 ("hides unavailable actions without relying on hidden UI
as security").

## Multi-line transaction scope checks

A bulk transfer or multi-line assignment/delivery touches several `Location`/`UnitAsset`/`StockBalance` rows in one
transaction. The service layer checks `require_location_access` for **every** source and destination location of
**every** line before writing anything — a user cannot smuggle an out-of-scope line into an otherwise-authorized
transaction.

## Country/storage scope in exports and reports

`reporting` app views build their base queryset via `scope_queryset` before applying any filter/sort/pagination, so
CSV/XLSX export uses the identical filtered queryset the on-screen list used — there is no separate "export"
code path that could diverge and leak unauthorized rows (spec §4, acceptance criterion §21.3).

## Default admin bootstrap (added on direct user request, post-Prompt 9)

The user asked for installation to be a single command, ending with a working, loggable-in system — specifically:
a default Administrator account (`admin` / `admin`, both env-overridable via `BOOTSTRAP_ADMIN_USERNAME`/
`BOOTSTRAP_ADMIN_PASSWORD`) created automatically, that **must** be changed before anything else can be done.

This is a real, explicitly acknowledged security trade-off — predictable default credentials are a known
anti-pattern — accepted here because it's paired with genuine enforcement, not just a UI hint:

- `apps/accounts/management/commands/bootstrap_admin.py` is **idempotent**: it does nothing if any Administrator
  (or superuser) already exists. It's safe to run on every container start (`deploy/entrypoint.sh` runs it after
  every `migrate`) — it can never reset an operator's already-changed password back to the default.
- `MustChangePassword` (a row, not a flag on `User`, so it survives independently of any future custom-user-model
  work) is created alongside the bootstrap account. `apps.accounts.middleware.RequirePasswordChangeMiddleware`
  blocks **every** authenticated request except the password-change page, logout, and static assets while that row
  exists — not a dismissible banner, a hard redirect enforced server-side on every request, the same "never rely on
  hidden UI as security" principle this whole document is about.
- `django-axes` (doc 08) already rate-limits repeated login attempts against the known username, including
  `admin`, from the moment the container starts.
- Still a real residual window: if the deployed instance is reachable from an untrusted network before the
  operator's first login, `admin`/`admin` is guessable during that window. `deploy/DEPLOYMENT.md` calls this out
  explicitly as a step to complete before exposing the instance beyond a trusted network, and `bootstrap_admin` can
  be disabled entirely (`BOOTSTRAP_ADMIN_ENABLED=false`) for an operator who prefers the previous interactive
  `createsuperuser` flow instead.
