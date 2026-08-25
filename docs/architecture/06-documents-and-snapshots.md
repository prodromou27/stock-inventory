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

## Editable document templates (added on direct user request, after Prompt 9)

The section above describes swapping the packaged template via a code deployment. The user separately asked for an
Administrator to be able to preview and edit the assignment/delivery PDF templates **from the app itself** — layout,
wording, and a company logo — without a deployment, and to map the same data fields the packaged template already
uses. Delivered as `DocumentTemplate` (doc 02): one optional row per `DocumentType`, holding raw Django-template-
syntax HTML (`html_source`) and an optional logo file, editable at **Document Templates** in the nav
(Administrator-only).

- `apps/documents/pdf.py::render_pdf()` checks for an active `DocumentTemplate` row for the transaction's
  `document_type` first, and only falls back to the packaged `form_v1.html` file when none exists — so this is
  purely additive; nothing changes for an installation where no Administrator ever touches it.
- The context dict rendered into the template is **exactly** `build_document_context()`'s output (the same
  snapshot data the packaged template already uses) plus one new key, `logo_data_uri` — the uploaded logo embedded
  as a base64 data URI (WeasyPrint renders from an HTML string server-side, not a served page, so a data URI is the
  simplest way to embed an image regardless of `MEDIA_URL` policy — media is still never served directly). Every
  value in that context is a plain string/number/list, never a live model instance, which is also what keeps a
  user-authored template safe to render (see "Trust model" below).
- **Validated at save time, not discovered at print time**: `apps/documents/template_services.py::update_template()`
  renders the submitted HTML against realistic sample data (`pdf.sample_document_context()`) before saving —
  a broken template is rejected with the render error shown inline, and the previously saved (working) template is
  left untouched.
- **Preview without saving**: the edit screen's Preview button posts the in-progress (possibly unsaved) template
  text to a dedicated endpoint that renders it against the same sample data and returns a real PDF, opened in a new
  tab — so an Administrator can iterate before committing a change that affects real printed documents.
- **Reset to packaged default**: deletes the `DocumentTemplate` row entirely (not a soft toggle) — the next
  document generated for that type uses the packaged file template again, unchanged.

**Trust model**: rendering an Administrator-authored template via Django's template engine
(`django.template.Template(html_source).render(...)`) is deliberately not sandboxed further than Django's engine
already is. This is considered safe here because (a) Django's template language has no arbitrary code execution —
no function calls with arguments, no attribute access starting with `_`, no imports — unlike e.g. Jinja2's default
(unsandboxed) mode; (b) the context passed in is always plain snapshot data, never a live ORM object with callable
methods, so there's nothing for a template to reach back into the database through; and (c) this screen is already
Administrator-only, the same trust level that already has unrestricted `/admin/` access and can perform corrections
and reversals across the whole ledger. Documented explicitly here rather than left implicit, per spec §23.10's
"don't silently invent business rules" — a lower-trust role must never be given access to this screen without
revisiting this reasoning.

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
with a fresh `document_number` and a `supersedes` foreign key pointing back at the one it replaces (implemented as
a real self-FK rather than a free-text note, for queryability); nothing is overwritten in place.

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
