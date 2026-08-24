# Unit vs. Quantity Tracking, and Duplicate Detection

Covers spec §2.2–§2.3, §5, §6, Prompt 3.

## Unit-tracked vs. quantity-tracked

`Product.tracking_method` (`unit` | `quantity`) decides which of two independent data paths a receipt/movement
takes:

- **Unit**: one `UnitAsset` row per physical item, `quantity` is implicit (always effectively 1), status/location
  live on the asset itself.
- **Quantity**: no per-item row; a `StockBalance(product, location)` balance is moved by signed ledger deltas.

Both paths write to the *same* `InventoryTransaction`/`InventoryTransactionLine` ledger, so a single transaction
can freely mix line types (e.g., "assign 1 firewall + 2 toner cartridges to Employee X" is one transaction, one
document, two different line shapes) — this is what makes the multi-line acceptance criterion (§21.6) possible
without two parallel transaction models.

**Lock-in**: `tracking_method` is chosen when the product is created and becomes immutable the moment any
`UnitAsset` or `InventoryTransactionLine` references the product (checked in `ProductService.update()`). Changing
it afterward requires `ProductService.migrate_tracking_method()`, an Administrator-only operation that:

1. Requires the product to currently have zero `on_hand_quantity`/zero non-terminal `UnitAsset`s (i.e., nothing
   currently "live" to convert) OR explicit per-item conversion instructions if a reviewer wants that supported —
   flagged as an open question in doc 10, since the spec requires the *capability* to migrate (§5) but doesn't
   specify how in-flight stock should convert.
2. Writes an audited `InventoryTransaction(movement_type='correction')` documenting the change.

## Duplicate vendor serial numbers

Serials are **allowed** to repeat (§5) — this is not an error state, it's an expected reality of vendor numbering.
What must happen is *detection and acknowledgement*, not prevention:

1. On `UnitAsset` create/edit, `InventoryService.check_duplicate_serial(vendor_serial, exclude_id=None)` normalizes
   the input (`upper().strip()`, collapse internal whitespace) and queries `UnitAsset` by `normalized_serial` —
   scoped to locations the current user can see, since showing an out-of-scope match would itself be a scope leak.
2. If matches exist, the form re-renders with the matching records shown (product, current status, location,
   arrival date — enough to tell the user whether this is really the same item mis-entered or a legitimate vendor
   repeat) and a required "I confirm this is a separate item" checkbox/field.
3. Only after that explicit acknowledgement does `save()` proceed, and the acknowledgement is written as an
   `AuditEvent(event_type='duplicate_serial_acknowledged', metadata={"matched_ids": [...]})` plus
   `InventoryTransaction.duplicate_serial_acknowledged=true` on whichever transaction the create/receipt belongs to
   (spec §5, §12 — "the duplicate acknowledgement and user must be recorded in the audit log").
4. The "Duplicate serial flag" search filter (§14) queries `UnitAsset` grouped by `normalized_serial` having
   `count(*) > 1` within the user's scope, so previously-acknowledged duplicates stay discoverable later, not just
   at entry time.

This check runs both in the interactive receive-stock workflow and inside the Excel importer (doc 07), sharing the
same `check_duplicate_serial` function so behavior never diverges between the two entry points.

## Duplicate product detection

Product duplicate detection compares normalized `Brand` + `Model` + `SKU` (§6):

1. `CatalogService.check_duplicate_product(brand, model, sku)` normalizes the same way as `UnitAsset` serials
   (lower/trim/collapse-whitespace) and queries `Product.normalized_model`/`normalized_sku` within the same brand.
2. On a match, the create form shows the matching product(s) and requires acknowledgement before creating a new,
   separate `Product` row — same acknowledge-and-record pattern as serials, logged as
   `AuditEvent(event_type='record_created', metadata={"duplicate_acknowledged": true, "matched_ids": [...]})`.
3. No DB uniqueness constraint blocks this — matching §6 ("authorized users may acknowledge and create a legitimate
   duplicate").

## Why acknowledgement lives in `AuditEvent`, not a boolean on the row itself

A `UnitAsset`/`Product` might match zero, one, or several other rows at creation time, and that set can change
later as more rows are added — "was this a duplicate" is a fact about *the moment of creation*, not an ongoing
property of the row. Storing it as an audit event with the matched-IDs snapshot preserves exactly what the user saw
and confirmed, permanently, without needing a mutable "duplicate" flag on the row that could go stale.
