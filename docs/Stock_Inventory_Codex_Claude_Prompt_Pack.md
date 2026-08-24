# Stock Inventory Application — Codex and Claude Code Prompt Pack

Use these prompts in sequence. The same prompts work with Codex and Claude Code.

Before starting, place the approved specification in the repository at:

`docs/stock-inventory-build-spec.md`

The source document is `Stock_Inventory_Application_Build_Specification.md`.

Do not run every phase as one unattended request. Complete and review each phase before sending the next prompt.

## Common rules for every prompt

The following rules are already included conceptually in each phase, but may be pasted before any prompt when starting a new tool session:

```text
Read docs/stock-inventory-build-spec.md completely before making changes. Treat it as the authoritative business specification.

Inspect the repository and all repository instruction files before acting. Preserve existing user changes and do not perform destructive Git operations. Do not commit or push unless I explicitly ask.

Use a modular Django monolith, PostgreSQL, server-rendered templates, HTMX only where it improves interaction, and Docker Compose. Keep domain rules in transactional application services rather than views, forms, signals, or templates.

For this task:
1. State your implementation plan and the files you expect to change.
2. Identify any conflict between the repository and the specification.
3. Implement only the requested phase.
4. Add or update automated tests.
5. Run the relevant formatting, static checks, migrations checks, and tests.
6. Fix failures caused by your changes.
7. Finish with a concise summary of changed files, commands run, test results, assumptions, and remaining risks.

Do not silently invent business rules. If a missing decision materially affects inventory integrity, permissions, or historical records, stop and ask a focused question.
```

---

## Prompt 0 — Architecture and implementation plan

Use this first. It requests design artifacts only, not application implementation.

```text
Read docs/stock-inventory-build-spec.md completely and inspect the current repository. Do not implement the application yet.

Produce a concrete technical implementation plan for the stock inventory application using Django, PostgreSQL, HTMX where useful, PDF generation, and Docker Compose.

Deliver the following as repository documentation:

1. Proposed repository and Django app structure, with responsibilities for each app/module.
2. Entity-relationship model covering locations, products, unit assets, quantity balances, inventory transactions and lines, documents, attachments, imports, access scopes, and audit events.
3. Field-level model proposal with important indexes, unique constraints, check constraints, and deletion policies.
4. Status-transition table for In Stock, Reserved, Assigned, Delivered, Returned, Damaged, Lost, and Disposed.
5. Movement rules for receipts, transfers, reservations, assignments, deliveries, partial returns, damage, loss, disposal, corrections, and reversals.
6. Role and location-scope permission matrix for Administrator, Stock Manager, and Read-only User.
7. Strategy for duplicate vendor serial numbers: allow duplicates, show matches, require acknowledgement, and audit the acknowledgement.
8. Strategy for unit-tracked versus quantity-tracked products.
9. Historical snapshot strategy for immutable printable documents.
10. Excel import staging, validation, idempotency, and rollback approach.
11. Security, backup, testing, observability, and performance plan.
12. Phased delivery backlog with dependencies and acceptance criteria.

Prefer simple, maintainable designs. Do not introduce microservices, a SPA, Redis, Celery, or a public API unless you demonstrate a current requirement for them.

List any questions that truly block implementation. Separate non-blocking assumptions from blockers. Save the plan under docs/architecture/ using clear Markdown filenames.

Run no destructive commands and make no production configuration changes. Finish by summarizing the proposed architecture and the first implementation phase.
```

---

## Prompt 1 — Repository foundation and Docker environment

```text
Read docs/stock-inventory-build-spec.md and the approved documents under docs/architecture/. Inspect the repository before changing it.

Implement Phase 1 foundation only:

- Django project and modular app structure approved in the architecture plan
- PostgreSQL configuration
- Dockerfile and Docker Compose development environment
- Environment-based configuration with a committed .env.example containing no secrets
- Local username/password authentication
- Base layout and navigation shell
- Health/readiness endpoint suitable for Docker health checks
- pytest/pytest-django configuration
- Formatting and static-analysis configuration
- Initial CI-friendly commands
- Structured application logging
- Development seed command for one Administrator, one Stock Manager, and one Read-only User, without hard-coded production passwords

Create concise repository guidance for both Codex and Claude Code if not already present. Keep AGENTS.md and CLAUDE.md consistent with the project commands and architecture.

Security requirements:

- Use secure framework defaults.
- Secrets must come from environment variables.
- Production DEBUG must default to false.
- Configure allowed hosts, CSRF trusted origins, secure cookies, and proxy/TLS settings through environment variables.
- Do not expose stack traces in production.

Add tests for application startup, health checks, authentication, and production-setting validation. Generate and verify migrations. Start the Docker stack if the environment permits, run tests, and report any environment limitation accurately.

Do not implement inventory models or workflows yet except minimal shared abstractions approved in the architecture plan.
```

---

## Prompt 2 — Locations, users, and scoped permissions

```text
Read docs/stock-inventory-build-spec.md and the architecture documents. Implement the location hierarchy and access-control foundation.

Required hierarchy:

Country -> Site/Building -> Floor -> Storage Room -> Rack/Cabinet -> Shelf/Bin

Lower levels are optional. Referenced locations may be deactivated but not deleted. The schema must support multiple countries even though the initial deployment uses one country and one building.

Implement:

- Location models, migrations, administration, forms, list/detail screens, and active/inactive behavior
- Administrator, Stock Manager, and Read-only roles
- Explicit user access to one or more country/storage scopes
- A centralized authorization-scope service/query layer
- Server-side enforcement for HTML views, object lookups, search results, exports, reports, and attachment placeholders
- Navigation that hides unavailable actions without relying on hidden UI as security
- Audit events for location, role, user-access, and relevant account changes

Do not duplicate authorization logic across views. Provide reusable scoped query functions/managers and permission checks.

Add tests proving:

1. Users see only permitted locations and records.
2. Direct object URLs cannot bypass scope restrictions.
3. Read-only users cannot mutate records.
4. Stock Managers cannot administer users or permissions.
5. Administrators can manage scope access.
6. Deactivation preserves historical references.

Include seed data for a realistic one-country building with a second-floor storage room, rack, and shelf.
```

---

## Prompt 3 — Product catalog and inventory ledger

```text
Read docs/stock-inventory-build-spec.md and all approved architecture documents. Implement the product catalog and inventory ledger foundation.

Implement products with separate Brand, Model, optional SKU, Type/Category, Description, unit-or-quantity tracking method, optional Supplier, optional low-stock threshold, notes, and active status.

Implement unit-tracked assets:

- One database record per physical unit
- Internal UUID primary key that is not presented as a user asset tag
- Quantity fixed at one
- Optional vendor serial number
- Duplicate vendor serials allowed
- Duplicate search normalized for whitespace and case
- Visible duplicate warning showing matching authorized records
- Explicit acknowledgement required before saving a duplicate
- Audit event for the acknowledgement
- Current status and optional location
- Manual Project Reference and Final Customer
- Optional Supplier and Invoice Number
- Arrival date, condition, accessories, and notes

Implement quantity-tracked inventory:

- Balance by product and location
- Available and reserved quantities
- Ledger-backed receipt and movement lines
- No negative stock, except an explicit Administrator correction with an audit event

Implement InventoryTransaction and InventoryTransactionLine as the authoritative ledger. Completed transactions must be immutable through ordinary editing. Include historical display snapshots required by later document generation.

Keep business rules in atomic application services. Do not use Django signals for inventory balance changes.

Create admin and desktop-oriented user interfaces for product and inventory creation, list, filter, detail, and history views. Use server-side pagination and indexed queries.

Add model, service, authorization, concurrency, and UI tests. Test duplicate serial acknowledgement, quantity integrity, transaction atomicity, and tracking-method restrictions after movements exist.
```

---

## Prompt 4 — Inventory movement workflows

```text
Read docs/stock-inventory-build-spec.md and inspect the existing ledger implementation. Implement the supported inventory workflows through transactional services and desktop-oriented UI.

Required workflows:

1. Receipt into stock
2. Project/customer reservation
3. Reservation release
4. Bulk location transfer
5. Employee assignment, temporary or permanent
6. Customer delivery
7. Partial and complete return
8. Return assessment
9. Mark damaged
10. Mark lost
11. Dispose
12. Administrator correction and reversal

Rules:

- Stock Managers complete movements directly without secondary approval.
- Each transaction may contain multiple product and asset lines.
- Project Reference and Final Customer are manual text values; do not build CRM integration.
- An assigned employee is entered manually.
- Reservation keeps stock physically in place but removes it from generally available stock.
- Condition and accessories are captured during assignment/delivery and return.
- Temporary assignment and optional expected return date are recorded, but no overdue notification is needed.
- Partial returns leave unreturned lines assigned or delivered.
- Returned items await assessment and then become In Stock, Damaged, or Disposed.
- Removal Date is created whenever an asset physically leaves storage and remains in history after return.
- Disposed records, particularly HDDs, remain searchable.
- Invalid status transitions are rejected server-side.
- Completed movements cannot be silently edited or deleted.
- Administrator corrections/reversals preserve the original transaction and full audit history.
- Lock relevant database rows during balance-changing operations to prevent concurrent overspending.

Create clear confirmation and result pages. Show the transaction number after completion. Make validation messages actionable.

Add comprehensive service and browser-level tests for every transition, bulk transfer, mixed multi-line transactions, partial returns, insufficient quantity, scope violations, concurrency-sensitive balance updates, correction, and reversal.
```

---

## Prompt 5 — Printable forms, PDFs, and attachments

```text
Read docs/stock-inventory-build-spec.md. Implement document generation and protected attachments for completed employee assignments and customer deliveries.

The final company template is not yet available. Build a replaceable HTML-to-PDF template layer using WeasyPrint or the already approved PDF engine.

Each generated form must include:

- Unique sequential document number
- Transaction type and date
- Employee or Final Customer
- Project Reference
- Source storage location
- Multi-line table with Brand, Model, SKU, Type/Description, Serial, Quantity, condition, and accessories
- Notes
- Authenticated user who prepared the record
- Delivered-by and received-by printable signature lines
- Signature date fields

Requirements:

- Generate the PDF from immutable transaction-line snapshots.
- Later product edits must not alter an old document.
- Store the generated file and metadata securely.
- Allow an optional scanned signed form to be uploaded and linked to the transaction.
- Validate extension, MIME type, filename, and configured size limit.
- Never expose uploads through an unauthenticated public media path.
- Enforce country/storage scope on every preview and download.
- Never overwrite an attachment silently.
- Audit generation, upload, download where appropriate, and deletion/deactivation actions.
- Electronic signatures are out of scope.

Create tests for snapshot immutability, sequential numbering, authorization, malicious filenames, invalid uploads, and PDF generation. Where exact PDF rendering is difficult to assert, test the HTML context and verify that a non-empty valid PDF is produced.
```

---

## Prompt 6 — Excel/CSV import and normalized export

```text
Read docs/stock-inventory-build-spec.md, especially the Excel mapping and import requirements. Implement a safe staged XLSX/CSV importer for the legacy inventory.

Expected legacy columns:

BRAND
MODEL/Part No./SKU
TYPE/DESCRIPTION
S/N
QTY
LOCATION
2nd floor Location
Project Ref. #
FINAL CUSTOMER
COMMENTS/#No
PRODUCT DELIVERY / PRODUCT REMOVAL
Arrival Date
Delivery Date
Return Date
Removal Date
Registrar

Implement:

1. Upload to an import batch without immediately changing live inventory.
2. Header mapping and preview.
3. Preservation of source filename, source row number, original cell values, importing user, and timestamps.
4. Whitespace, serial, quantity, and date normalization without losing originals.
5. Validation for required product fields, quantities, dates, tracking-method ambiguity, locations, duplicate products, and duplicate serials.
6. User correction/mapping during preview, including Model versus optional SKU interpretation.
7. Import execution using transactional or safely resumable batches.
8. Row-level outcomes: imported, skipped, warning, failed.
9. Idempotency so retrying the same batch/row cannot duplicate all inventory.
10. Downloadable results report.
11. Audit events for upload, validation, execution, retry, and export.

Do not guess the meaning of ambiguous legacy values. Flag them for review. Do not create a CRM or validate Project References externally.

Implement a new normalized export format for inventory and reports; reproducing the legacy Excel layout is not required.

Add fixture workbooks and tests covering malformed headers, duplicate rows, duplicate serials, mixed date formats, blank optional locations, invalid quantities, interrupted/retried batches, authorization, and large-file pagination/batching.
```

---

## Prompt 7 — Dashboard, search, reports, and disposal reporting

```text
Read docs/stock-inventory-build-spec.md. Implement the dashboard, inventory search, reporting, and normalized exports.

Required search filters:

- Free text
- Brand, Model, SKU, Type
- Vendor serial
- Status
- Country/Site/Floor/Room/Rack/Shelf
- Project Reference
- Final Customer
- Arrival, removal, and delivery date ranges
- Supplier and Invoice Number
- Duplicate serial flag

Required reports:

- Current stock
- Stock by room/location
- Reserved stock
- Employee assignments
- Customer deliveries
- Stock by Project Reference
- Temporary assignments without overdue automation
- Damaged assets
- Lost assets
- Disposed items, optimized for reviewing disposed HDDs
- Complete asset movement history

All results and exports must enforce user location scope. Use server-side pagination, stable sorting, indexed filters, select_related/prefetch_related where appropriate, and query-count tests for important screens.

The disposed-items report must show enough information to identify each HDD and its disposal transaction, including Brand, Model, SKU, serial, Project Reference, Final Customer, disposal date, reason/notes, and authenticated user. Do not implement mandatory sanitization certificates yet, but keep the design extensible.

Optional low-stock thresholds may be configured per quantity-tracked product. If no threshold is configured, show no alert. Do not add email or external notifications.

Add tests for filters, date boundaries, authorization leakage, normalized exports, disposed HDD reporting, pagination, and representative performance with at least 8,000 seeded records.
```

---

## Prompt 8 — Security, performance, backup, and production deployment

```text
Read docs/stock-inventory-build-spec.md and review the implemented application as a production candidate for internal Docker deployment.

Implement and verify:

- Secure local username/password authentication
- Current recommended password hashing
- Login throttling or lockout protection
- CSRF and session security
- Configurable session timeout and password policy
- Server-side role and storage-scope authorization on every endpoint
- Protected file downloads
- Safe upload validation
- Production-safe error handling and logging
- Environment-based secrets
- Database constraints and transaction boundaries
- PostgreSQL indexes for serial, Brand/Model/SKU, Project Reference, Final Customer, status, location, and movement dates
- Query optimization and server-side pagination
- Docker health checks and graceful startup
- Production Docker Compose example and reverse-proxy guidance
- Daily database and attachment backup scripts/procedure
- Documented restore procedure and a restore verification test in a disposable environment
- Data migration and deployment runbook

Run security-focused tests and inspect for object-level authorization bypass, insecure direct-object references, mass-assignment issues, unprotected exports, attachment traversal, unsafe file names, and accidental secret exposure.

Seed or generate at least 8,000 representative records and measure critical list/report queries. The normal target is under one second on reasonable internal infrastructure, but report actual environment and results rather than fabricating numbers.

Do not deploy to a real server or change external infrastructure. Produce production-ready configuration and instructions only unless I separately authorize deployment.
```

---

## Prompt 9 — Final acceptance and release audit

```text
Act as the final technical reviewer for this stock inventory application. Read docs/stock-inventory-build-spec.md, all architecture documents, migrations, application code, tests, Docker configuration, and deployment documentation.

Do not begin by rewriting the application. First build a traceability matrix mapping every minimum acceptance criterion and explicit out-of-scope item in the specification to implementation files and tests.

Then:

1. Run the full automated test suite, migration checks, formatter, linter/static checks, and security checks.
2. Test all three roles and cross-country/storage access boundaries.
3. Test serialized and quantity inventory from receipt through removal and return.
4. Test duplicate vendor serial acknowledgement.
5. Test bulk transfer, reservation, mixed-line assignment/delivery, partial return, disposal, correction, and reversal.
6. Test immutable PDF snapshots and signed attachment authorization.
7. Test import preview, error handling, idempotent retry, and normalized exports.
8. Test disposed HDD reporting.
9. Test with at least 8,000 records and report measured query counts/timings.
10. Review backup/restore and production configuration.

Classify findings as Critical, High, Medium, or Low. Fix Critical and High issues within the approved specification, add regression tests, and rerun validation. Do not add out-of-scope features.

Produce:

- Acceptance traceability matrix
- Test and check results
- Remaining findings and risks
- Deployment prerequisites
- Administrator setup instructions
- Stock Manager quick-start instructions
- Release recommendation: Ready, Ready with conditions, or Not ready

Do not claim readiness when checks were skipped. Clearly distinguish verified behavior from assumptions or environment limitations.
```

---

## Optional prompt — Review an existing implementation before continuing

Use this if one tool has already written code and you want the other tool to review it.

```text
Read docs/stock-inventory-build-spec.md and inspect the existing implementation and Git diff. Treat all current user changes as work to preserve.

Review the implementation for correctness against the specification. Concentrate on inventory integrity, status transitions, transaction atomicity, partial returns, duplicate vendor serial handling, historical snapshots, object-level location permissions, audit completeness, import idempotency, and protected attachments.

Do not make cosmetic changes first. Report findings ordered by severity with exact file references, impact, and a proposed fix. Identify missing tests separately.

After presenting the findings, fix only Critical and High issues that are unambiguously within scope. Add regression tests and run the relevant checks. Do not replace working architecture or introduce new frameworks without a demonstrated requirement.

Finish with the issues fixed, issues intentionally left, commands run, test results, and any decision required from me.
```

---

## Optional prompt — Add the final company PDF template later

```text
The company assignment/delivery form template is now available at [INSERT FILE PATH]. Read it together with docs/stock-inventory-build-spec.md and inspect the existing document-generation implementation.

Replace the temporary printable template with a faithful application template while preserving:

- Existing transaction and document identifiers
- Immutable historical snapshots
- Multi-line asset/product support
- Employee and customer variants
- Project Reference
- Condition and accessories
- Printable signature fields
- Optional signed-document upload
- Existing authorization and audit behavior

Do not alter inventory workflow rules. Render representative one-page and multi-page documents, visually inspect the output, fix clipping/page-break/table-header issues, and add regression tests. Preserve old generated PDFs; the new template applies only to newly generated documents unless I explicitly request regeneration.
```

## Recommended operating sequence

1. Prompt 0 — approve architecture.
2. Prompt 1 — foundation.
3. Prompt 2 — permissions and locations.
4. Prompt 3 — product and ledger.
5. Prompt 4 — movements.
6. Prompt 5 — documents.
7. Prompt 6 — import/export, preferably after supplying a sanitized copy of the real workbook.
8. Prompt 7 — reports.
9. Prompt 8 — production hardening.
10. Prompt 9 — acceptance audit.

Use the cross-tool review prompt after any major phase when switching between Codex and Claude Code.
