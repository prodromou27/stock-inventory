# Immutable Documents and Attachments

Covers spec §10–§11, Prompt 5.

## Snapshot strategy

The spec requires previously generated PDFs to never change when the underlying `Product`/`UnitAsset` data is
edited later (§10). This is achieved at two layers, deliberately redundant:

1. **`InventoryTransactionLine` snapshot fields** (doc 02) — Brand/Model/SKU/Type/Description/Serial/Project
   Reference/Final Customer/Supplier/Invoice Number/condition/accessories are copied onto the line at the moment
   the transaction is completed, and never re-read from `Product`/`UnitAsset` afterward. This is the data the
   document renderer uses.
2. **`GeneratedDocument.context_snapshot`** (JSONB) — the exact dict passed into the PDF template at render time,
   stored alongside the file itself. Even if a future template change would render the same transaction-line data
   differently, the *originally rendered* context is preserved for audit/reproduction purposes, and the PDF file
   itself is the ultimate immutable artifact.

`document_type` (`assignment` | `delivery`) selects between two small template variations (employee vs. final
customer, otherwise identical layout) of one shared base template, per spec §10's shared field list.

## Template replaceability

The HTML template lives at `apps/documents/templates/documents/pdf/<document_type>_v<N>.html`. `GeneratedDocument.
template_version` records which one rendered a given document. When "Prompt — Add the final company PDF template
later" is run, a new versioned template file is added (not an edit to the old one), the rendering service starts
using it for new documents, and every previously generated `GeneratedDocument` keeps pointing at its original PDF
file — satisfying "the new template applies only to newly generated documents unless explicitly requested."

## Rendering pipeline

`DocumentService.generate(transaction) -> GeneratedDocument`:

1. Assert the transaction's `movement_type` is `assignment` or `delivery` (only those produce a printable form,
   per §10).
2. Build the context dict directly from `InventoryTransactionLine` snapshot fields (never from live `Product`/
   `UnitAsset` queries).
3. Allocate the next `document_number` from a dedicated Postgres sequence.
4. Render HTML → PDF via WeasyPrint, store the file in the protected volume under a path keyed by
   `GeneratedDocument.id` (not by any user-controlled filename).
5. Persist `GeneratedDocument` (including `context_snapshot`) and write an `AuditEvent(event_type=
   'document_generated')`, all in one transaction.

A "regenerate" action (e.g., if the first PDF render had a template bug) creates a **new** `GeneratedDocument` row
with a fresh `document_number` and a note referencing the superseded one; nothing is overwritten in place.

## Attachments

`AttachmentService.upload(transaction, file, user)`:

1. Validates extension + sniffed MIME type against an allow-list (PDF and common image formats for scanned signed
   forms) and file size against a configured max — both server-side, never trusting the client `Content-Type`.
2. Generates a storage filename from `Attachment.id` (never the user-supplied filename) to eliminate path-traversal
   and collision risk; `original_filename` is kept only as display metadata.
3. Stores the file in the protected volume (outside any path Django/nginx serves directly) — download requires a
   dedicated view that reapplies `require_location_access` on the attachment's transaction before streaming the
   file (spec §11 — "require authorization before download").
4. Writes an `AuditEvent(event_type='attachment_uploaded')`.

A second upload on the same transaction is always a new `Attachment` row — never an overwrite (§11, explicit).
Deletion is soft (`is_deleted=true`), Administrator-only, and itself audited (`event_type='attachment_deleted'` —
the underlying file is retained, not purged, consistent with "permanent deletion... is prohibited," §12, applied
here by extension even though §12 names inventory/audit history specifically).

## Download authorization

Both `GeneratedDocument` and `Attachment` downloads go through the same `core.scoping.require_location_access`
check used everywhere else, keyed off the parent `InventoryTransaction`'s locations — there is no separate,
divergent authorization path for file downloads (spec §11, §17 security requirement "authorization on every...
attachment download").
