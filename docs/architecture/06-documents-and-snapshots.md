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
Administrator to be able to preview and edit the assignment/delivery PDF templates from the app itself, including
layout, wording, and a company logo, without a deployment. Delivered as `DocumentTemplate` (doc 02), the editor is
available at **Document Templates** in the nav and remains Administrator-only for changes.

It is deliberately a structured editor rather than a raw HTML/CSS editor. It safely controls branding,
document/company wording, section order, line-column order and labels, margins, colours, typography, table spacing,
notes, terms, and signature labels. `html_source` is generated from these validated settings, so Administrators can
create a complete supported form without introducing executable template code or unsafe CSS.

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
- **Preview without saving**: the editor posts in-progress structured settings to a dedicated endpoint. It provides
  a sandboxed live HTML preview and can open the corresponding PDF preview against the same sample data, so an
  Administrator can iterate before committing a change that affects real printed documents.
- **Versioning and status**: templates are Draft or Published. Each save creates an append-only
  `DocumentTemplateVersion`; restoring a version creates a new version rather than changing history. Reset and delete
  deactivate the editable template, so the packaged default is used for new documents while the audit trail remains.

**Trust model**: only server-owned templates and section partials are passed to Django's template engine. The
Administrator can supply presentation values only, which are validated and escaped through the normal form/template
pipeline. This prevents arbitrary template syntax, scripts, and free-form CSS from entering a printed form. The
screen remains Administrator-only; Stock Managers have read-only preview access.

## Rendering pipeline

### Embedded GrapesJS designer (September 2026)

On explicit Administrator approval, printed content is entirely optional in the new
visual designer: document numbers, dates, prepared-by text, tables and signatures may
all be removed. This supersedes the fixed printed-field checklist for visual designs
only. Transaction data, numbering, authorization and immutable generated documents are
unchanged. Publishing still requires PDF review and the existing approval workflow.

`/documents/templates/<type>/designer/` embeds locally bundled GrapesJS core. The
Administrator edits HTML-like layout blocks, not Django source. The service in
`apps/documents/designer_services.py` validates markup, bounded CSS and allow-listed inventory
tokens, then emits the server-owned Django expressions and repeating asset-row loop.
Scripts, event handlers, arbitrary template expressions and external images/styles
are not accepted. A company-logo block uses the existing validated logo upload.
Designer HTML/CSS is saved in `layout_config.visual_design` alongside compiled source
in each existing append-only version snapshot. Stale saves to existing templates are
rejected using their version number. Existing structured/source templates are not
automatically converted or overwritten on opening the canvas; replacement is explicit
on Save. Restoring or duplicating a visual design retains its editable layout.

Build local assets with `npm ci`, `npm run build:css`, and `npm run build:vendor`.
Both Docker builds, development Compose's asset watcher and CI run the vendor build.
GrapesJS is pinned in package-lock.json; no CDN, Studio SDK or telemetry is used.
The canvas represents the A4 printable area with 20 mm margins. PDF preview is the
authoritative pagination check; this is not a DOCX editor or a free-position DTP tool.

### No-code designer refinement (September 2026)

The editor now gives the paper preview most of the workspace and keeps properties in a
scrollable panel. Administrators can edit title/company wording, standing notes, terms and
column labels directly on the preview. Only plain text is copied to the existing validated
form fields; preview DOM/HTML is never persisted. Column visibility controls sit alongside
column labels and ordering. Existing source templates remain in a clearly marked optional
developer mode; switching back to the structured layout is explicit, not an automatic conversion.

Up to 12 custom text blocks can be placed before named document sections with left/center/right
alignment. The presentation validator limits each block to 5,000 characters and allow-lists
positions and alignment. Server-owned template markup escapes all block text. The configuration
is included in append-only template version snapshots and follows the existing preview/save/
publish/restore workflow. Saved source and generated PDFs are never rewritten by a UI upgrade.

The supplied acceptance/sign-off design is a selectable layout for Delivery, Assignment and
Disposal, not a delivery-only override. A shared preset supplies type-specific titles,
declarations and reference captions, plus logo/reference placement, date/title alignment,
four line-item columns and stacked sign-off fields. Customer/employee/witness values still
come from the transaction snapshot. The reference box uses the existing generated document
number; it does not introduce a second numbering sequence. Applying a preset to an existing
editor changes unsaved form values only; the normal save/version/publish rules still apply.

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

### Visual designer activation (approved workflow refinement)

The Administrator-only visual designer offers **Save & use for new documents**.
It compiles the restricted layout, validates normal and multi-page PDF rendering,
records an append-only version, and activates that version atomically with an audit
event. This explicit action does not require a second Administrator. The advanced
editor retains its existing draft/review workflow. Existing generated PDFs remain
immutable; regeneration creates a new document using the active template.

Both `GeneratedDocument` and `Attachment` downloads go through the same `core.scoping.require_location_access`
check used everywhere else, keyed off the parent `InventoryTransaction`'s locations — there is no separate,
divergent authorization path for file downloads (spec §11, §17 security requirement "authorization on every...
attachment download").
