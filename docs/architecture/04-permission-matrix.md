# Permission Matrix and Enforcement Strategy

Covers spec §4, Prompt 2.

## Two independent dimensions

Every authorization check is the AND of two independent checks:

1. **Role** — what kinds of actions the user's account can perform at all, from Django `Group` membership:
   `Administrator`, `StockManager`, `ReadOnlyUser` (exactly one per user; enforced by a form/admin validation, not a
   DB constraint, since Django doesn't make "exactly one group" trivial to express as a constraint).
2. **Location scope** — which `Location` subtrees the user may see/act on, from `UserLocationAccess` rows (doc 02),
   except Administrators, who may be granted `all_locations=true` instead of one row per country.

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

`core.scoping` provides:

- `accessible_locations(user) -> QuerySet[Location]` — every `Location` in the user's granted subtrees (or all, for
  `all_locations` Administrators).
- `scope_queryset(user, queryset, location_field="current_location")` — wraps any queryset (inventory, transactions,
  reports) with `WHERE <location_field>__path <@ ANY(accessible_paths)`, using the `ltree` ancestor operator so this
  is a single indexed filter, not a per-row Python check.
- `require_location_access(user, location)` — raises `PermissionDenied` for use before any write.
- `require_role(user, *allowed_groups)` — decorator/mixin for view classes.

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
