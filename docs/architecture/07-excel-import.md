# Excel/CSV Import

Covers spec §13, Prompt 6.

## Column mapping

| Excel column | Destination | Notes |
|---|---|---|
| BRAND | `Product.brand` (get-or-create `Brand`) | |
| MODEL/Part No./SKU | staged, then user-mapped to `Product.model` and optional `Product.sku` during preview | ambiguous by nature — never auto-split without a user decision (spec explicit) |
| TYPE/DESCRIPTION | `Product.product_type` (get-or-create `ProductType`) + `Product.description` | |
| S/N | `UnitAsset.vendor_serial` | triggers duplicate-serial check via the same `check_duplicate_serial` used interactively |
| QTY | `UnitAsset` count (one row per unit if serials given / product is unit-tracked) or `StockBalance` delta | tracking method ambiguity flagged for user choice when not inferable |
| LOCATION + "2nd floor Location" | `Location` resolution down the hierarchy | unknown locations reported, not silently created, except optionally by explicit user confirmation in preview |
| Project Ref. # | `project_reference` (manual text, no validation against an external system, per §2.6) | |
| FINAL CUSTOMER | `final_customer` (manual text) | |
| COMMENTS/#No | `notes` (append legacy reference number into the text) | |
| PRODUCT DELIVERY / PRODUCT REMOVAL | staged legacy status/movement value | mapped to a movement type only with explicit rules the user confirms in preview — see open question in doc 10 |
| Arrival Date | receipt `occurred_at` | |
| Delivery Date | delivery transaction `occurred_at` | |
| Return Date | return transaction `occurred_at` | |
| Removal Date | assignment/delivery/loss/disposal `occurred_at` (whichever applies) | |
| Registrar | preserved verbatim in `ImportRow.raw_data` and copied into `notes` as legacy traceability text — not mapped to `performed_by`, since the importing user (not the historical registrar) is the accountable actor for the import itself | |

## Pipeline

1. **Upload** → `ImportBatch` created with `status='uploaded'`, file stored, `file_checksum` computed. If a batch
   with the same checksum already reached `completed`, the UI warns before allowing a second run (protects against
   accidental re-upload of the same workbook, on top of the row-level idempotency below).
2. **Parse & stage** → one `ImportRow` per spreadsheet row, `raw_data` = verbatim cell values, `status='previewed'`
   once staging succeeds. No database mutation to `Product`/`UnitAsset`/`StockBalance` happens yet.
3. **Normalize** → `normalized_data` computed (trim whitespace, parse dates leniently across the formats seen in
   the legacy file, normalize serials) without discarding `raw_data`.
4. **Validate** (per row, non-destructive) → checks: required Brand/Model/Type present; quantity is a positive
   integer when given; dates parse; referenced location resolves (or is flagged unknown); tracking-method
   ambiguity flagged; duplicate product/serial candidates flagged (not blocked). Each row's `outcome` becomes one
   of `pending → warning` (needs a decision but importable) or `pending → failed` (cannot import as-is) at this
   stage — `imported` is only set after execution.
5. **Preview & correct** → mapping UI lets the user fix ambiguous Model/SKU splits, choose tracking method for
   ambiguous products, confirm or remap unknown locations, and resolve flagged duplicates row-by-row or in bulk.
   Corrections are stored back onto `ImportRow.normalized_data`/a mapping-override structure, not by mutating
   `raw_data`.
6. **Execute** → for rows not already `imported`, run the same `InventoryService` functions the interactive UI
   uses (receipt, and where a movement is inferable, the corresponding movement) inside batched transactions (e.g.
   500 rows per DB transaction, not one transaction for 8,000+ rows, to keep lock duration bounded) — never a
   parallel, import-only code path that could drift from interactive business rules.
7. **Result** → `ImportBatch` rolls up counts; a downloadable results file (CSV) lists every row's outcome and
   detail, in original row order, referencing `source row number` for cross-checking against the original file.

## Idempotency

- `ImportRow` is unique per `(batch_id, row_number)`.
- Re-running execution on the same batch **skips** any row whose `outcome` is already `imported` or `skipped`, and
  only attempts rows still `pending`/`warning` — so an accidental double-click or retried request cannot duplicate
  the rows that already succeeded (spec §13, explicit; acceptance criterion §21.14). As implemented (v1 scope,
  see doc 09), `failed` rows are excluded from retry within the same batch: a `failed` row's problem is with the
  row's own data (missing Brand, an unparseable quantity, a tracking-method conflict), and v1 has no in-place data
  -correction UI for a row — only a `warning` row's unresolved location can be fixed via an override, since
  `raw_data` is never mutated. Fixing a `failed` row means correcting the source file and uploading it again as a
  new batch (already a supported, audited workflow above).
- Each successful row records `created_unit_asset_id`/`created_transaction_id`, so "already imported" is a direct
  foreign-key check, not a heuristic re-match against inventory.
- A full re-upload of the same file produces a **new** `ImportBatch` (new checksum-flagged warning shown, but not
  blocked — a legitimate re-import of a corrected file is a valid workflow) rather than being merged into the old
  batch, keeping each batch's audit trail self-contained.

## Auditing

`AuditEvent` rows for: batch upload, validation run, execution start/finish (with counts), any retry, and results
export — matching spec §12's explicit list.
