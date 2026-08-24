# Stock Inventory Application — Build Specification

Version: 1.0  
Status: Approved baseline for implementation planning  
Target: Internal deployment using Docker  
Expected scale: 8,000+ inventory records, approximately 6 users

## 1. Purpose

Build an internal stock and technology-asset inventory application that records stock receipts, storage locations, reservations, assignments, customer deliveries, returns, damage, loss, and disposal.

The application must preserve a complete audit trail and generate printable stock assignment/delivery forms. It replaces the current Excel-based inventory process without becoming a CRM, accounting system, or procurement platform.

Typical inventory includes firewalls, switches, servers, HDDs, toner, keyboards, accessories, and other technology equipment.

## 2. Core principles

1. Inventory history must be transaction-based. Current status and location are outcomes of recorded movements.
2. Serialized and quantity-based products must be handled differently.
3. Vendor serial numbers are allowed to repeat, but duplicate serials must be clearly detected and acknowledged.
4. Completed actions must remain auditable.
5. Access must be restricted by role and by authorized country/storage scope.
6. Project References and Final Customers are entered manually. The application is not the master system for customer or project data.
7. The initial release must be desktop-oriented. Mobile optimization, offline use, barcode scanning, and electronic signatures are outside the initial scope.

## 3. Recommended technical architecture

Use a modular monolith with:

- Backend and server-rendered UI: Django 5.x
- Progressive interactivity: HTMX and small Alpine.js components where needed
- Styling: Bootstrap 5 or Tailwind CSS; choose one and use it consistently
- Database: PostgreSQL 16+
- Background jobs: not required initially; add a lightweight queue only if imports or document generation become slow
- Printable documents: HTML templates rendered to PDF using WeasyPrint
- File storage: protected local Docker volume initially, with a storage abstraction that can later support S3-compatible storage
- Deployment: Docker Compose behind an internal reverse proxy
- Authentication: local username and password using Django authentication
- Testing: pytest/pytest-django plus browser-level tests for critical workflows

This architecture is appropriate for 8,000+ records and six concurrent users, is straightforward to deploy internally, and avoids unnecessary frontend/API complexity. PostgreSQL pagination and indexed search must be used; the application must not load entire inventories into the browser.

## 4. Roles and permissions

### Administrator

- Full access to authorized or all country/storage scopes
- Manage users, roles, permissions, countries, sites, rooms, and sublocations
- Create and edit products and inventory
- Complete all movement types
- Correct completed transactions when required
- View the complete audit log
- Import/export data
- Configure optional low-stock thresholds

### Stock Manager

- Access only authorized country/storage scopes
- Create and maintain products and inventory within scope
- Receive, reserve, transfer, assign, deliver, return, damage, lose, and dispose of stock
- Complete transactions directly without secondary approval
- Generate and upload transaction documents
- Run permitted reports and exports
- Cannot manage users or alter audit records

### Read-only User

- View only authorized country/storage scopes
- Search inventory and view permitted reports/history
- Cannot create, edit, move, assign, deliver, return, or dispose of stock

### Access-control rules

- Each user has explicit access to one or more countries/storage scopes.
- A user must never retrieve unauthorized inventory through UI pages, exports, reports, direct URLs, or APIs.
- Permission checks must be performed on the server for every request.
- The audit log records the authenticated user; a separate physical handler field is not required.

## 5. Inventory tracking models

### Unit-tracked inventory

Used for serialized assets such as servers, switches, firewalls, and HDDs.

- One asset record represents one physical unit.
- Quantity is always 1.
- Vendor serial is optional unless required for a product category.
- Duplicate vendor serial numbers are permitted.
- On duplicate entry, show matching records and require explicit acknowledgement.
- The duplicate acknowledgement and user must be recorded in the audit log.
- Each record has an internal database UUID, but no user-facing internal asset tag is generated.

### Quantity-tracked inventory

Used for consumables or non-serialized products such as toner and some keyboards/accessories.

- Balances are maintained per product and location.
- Movements record positive or negative quantities.
- The system must prevent a negative balance unless an Administrator performs an explicitly logged correction.
- Partial deliveries and partial returns are supported.

The tracking method is selected on the product record and cannot be changed after movements exist without an Administrator migration operation.

## 6. Product and asset data

### Product fields

- Brand — required
- Model — required
- SKU — optional
- Type/category — required
- Description — optional
- Tracking method — unit or quantity
- Supplier — optional
- Default notes — optional
- Active/inactive flag
- Optional low-stock threshold

Brand, Model, and SKU are distinct fields. Product duplicate detection should compare normalized Brand, Model, and SKU, but authorized users may acknowledge and create a legitimate duplicate.

### Unit asset fields

- Internal UUID — system generated and not presented as an asset tag
- Product
- Vendor serial number
- Current status
- Current location, optional
- Project Reference, entered manually
- Final Customer company name, entered manually
- Supplier, optional
- Invoice number, optional
- Arrival date
- Condition
- Accessories
- Notes
- Created/updated timestamps and users

### Quantity stock fields

- Product
- Location
- Available quantity
- Reserved quantity
- Project Reference where applicable
- Final Customer where applicable
- Supplier, optional
- Invoice number, optional
- Notes

Balances must be calculated or maintained from immutable stock movement lines and regularly verifiable against the movement ledger.

## 7. Location model

Use the following fixed hierarchy:

1. Country
2. Site/building
3. Floor
4. Storage room
5. Rack/cabinet
6. Shelf/bin

Rules:

- The application initially operates in one country and one building, but the schema and permissions must support multiple countries and sites.
- Lower location levels are optional. An item may be recorded only at country, site, or room level.
- Locations can be deactivated but cannot be deleted when referenced by inventory or history.
- Bulk transfer of selected assets or quantities between locations is supported.
- Location transfers create audit/movement entries; no printable transfer form is required.

## 8. Statuses and movement types

### Current statuses

- In Stock
- Reserved
- Assigned
- Delivered
- Returned
- Damaged
- Lost
- Disposed

`Returned` represents an item received back and awaiting assessment. Once assessed, it should transition to `In Stock`, `Damaged`, or `Disposed`.

### Movement types

- Receipt into stock
- Location transfer
- Project/customer reservation
- Employee assignment
- Customer delivery
- Return
- Return assessment
- Mark damaged
- Mark lost
- Disposal
- Administrator correction
- Reversal

### Removal date

Removal Date is the date an item physically leaves storage. It is recorded for assignments, deliveries, loss, or disposal. It should be derived from the relevant completed movement rather than freely maintained as an unrelated asset field.

If an asset returns, the prior removal date remains in history. The asset can later receive another removal date through a new movement.

### Transition rules

- In Stock -> Reserved, Assigned, Delivered, Damaged, Lost, or Disposed
- Reserved -> In Stock, Assigned, Delivered, Lost, or Disposed
- Assigned -> Returned, Lost, Damaged, or Disposed
- Delivered -> Returned only when returns are allowed and recorded
- Returned -> In Stock, Damaged, or Disposed
- Damaged -> In Stock after repair, or Disposed
- Lost -> In Stock only through an Administrator correction/recovery movement
- Disposed is terminal except for an Administrator reversal

Invalid status transitions must be rejected server-side.

## 9. Main workflows

### Receive stock

1. Select or create a product.
2. Choose unit or quantity tracking according to the product.
3. Enter vendor serials for unit-tracked items.
4. Show and acknowledge possible duplicate serials.
5. Enter quantity for quantity-tracked items.
6. Select an optional location.
7. Enter Arrival Date, manual Project Reference, Final Customer, Supplier, Invoice Number, condition, accessories, and notes as applicable.
8. Complete a receipt movement and update current balances/status.

### Reserve stock

1. Select available units or a quantity.
2. Enter Project Reference and Final Customer.
3. Complete reservation.
4. Stock remains in its physical location but is excluded from generally available stock.
5. Reservation can be released, assigned, or delivered.

No automated expiry or overdue logic is required initially.

### Bulk location transfer

1. Select multiple eligible assets and/or product quantities.
2. Select destination location.
3. Add optional notes.
4. Validate user access to source and destination.
5. Complete a single transaction with multiple movement lines.

### Employee assignment

1. Select one or more assets or quantities.
2. Enter employee name/details manually.
3. Choose temporary or permanent assignment.
4. An expected return date is optional and informational; no overdue automation is required initially.
5. Record condition and accessories.
6. Enter Project Reference and notes.
7. Complete transaction directly as Stock Manager or Administrator.
8. Set Removal Date and generate printable assignment form.

### Customer delivery

1. Select one or more assets or quantities.
2. Enter Final Customer company and Project Reference manually.
3. Record condition, accessories, and notes.
4. Complete transaction directly.
5. Set Removal/Delivery Date and generate printable delivery form.

Customer address and contact person are not required.

### Partial return

1. Open the original assignment or delivery.
2. Select only the returned lines/units or enter returned quantities.
3. Record return date, condition, accessories returned, and notes.
4. Create a linked return transaction.
5. Leave unreturned lines assigned/delivered.
6. Set returned items to Returned pending assessment.

### Damage, loss, and disposal

- Record the reason, notes, date, and affected items.
- Disposal is especially important for HDD records.
- Disposed assets remain permanently searchable and reportable.
- The design should allow future optional HDD disposal metadata, such as sanitization method, certificate reference, and certificate attachment, without requiring it in the initial release.

## 10. Assignment and delivery documents

The final visual template will be supplied later. Until then, implement a replaceable HTML/PDF template containing:

- Unique sequential document number
- Transaction type
- Transaction date
- Employee or Final Customer
- Project Reference
- Source storage location
- Table of Brand, Model, SKU, Type/Description, Serial, Quantity, condition, and accessories
- Notes
- Prepared by/authenticated user
- Delivered by signature line
- Received by signature line
- Signature date fields

Requirements:

- One document may include multiple different assets/products.
- Generate a PDF snapshot from the completed transaction.
- Previously generated documents must not silently change when product data is edited later.
- A scanned signed document can optionally be uploaded and linked to the transaction.
- Electronic signatures are a future enhancement.

## 11. Attachments

- Allow optional attachments on transactions.
- Initial common use: signed assignment/delivery form.
- Validate file type and size.
- Store files outside public web paths and require authorization before download.
- Record uploader and upload timestamp.
- Never overwrite an existing attachment silently.

## 12. Audit and corrections

Audit at minimum:

- Login success/failure where appropriate
- Record creation
- Field changes with old and new values
- Movement completion
- Duplicate serial acknowledgement
- Document generation and attachment upload
- Import/export execution
- Permission and user changes
- Administrator corrections and reversals

Completed movements must not be normally editable. An Administrator may correct mistakes through a controlled correction or reversal action. The original values and movement remain visible. Permanent deletion of inventory history or audit entries is prohibited through the application.

## 13. Excel import

### Current columns

| Excel column | Destination |
|---|---|
| BRAND | Product.Brand |
| MODEL/Part No./SKU | Import staging field; map to Model and optional SKU using preview/correction |
| TYPE/DESCRIPTION | Product.Type and Description |
| S/N | UnitAsset.VendorSerial |
| QTY | Quantity or one unit record |
| LOCATION | Site/building/storage room mapping |
| 2nd floor Location | Floor/rack/shelf mapping |
| Project Ref. # | Manual Project Reference |
| FINAL CUSTOMER | Final Customer company name |
| COMMENTS/#No | Notes and legacy reference |
| PRODUCT DELIVERY / PRODUCT REMOVAL | Legacy status/movement staging value |
| Arrival Date | Receipt date |
| Delivery Date | Delivery movement date |
| Return Date | Return movement date |
| Removal Date | Removal movement date |
| Registrar | Legacy registrar text; preserve for traceability |

### Import process

1. Upload XLSX or CSV to a staging area.
2. Show a mapping and validation preview before database changes.
3. Normalize whitespace and dates without discarding original values.
4. Allow users to choose unit or quantity tracking for ambiguous products.
5. Detect duplicate product candidates and duplicate serials.
6. Report missing Brand/Model/Type, invalid quantities, invalid dates, and unknown locations.
7. Allow corrected mappings in the preview.
8. Execute the import in a database transaction or resumable batch.
9. Produce a results file containing imported, skipped, warning, and failed rows.
10. Preserve source filename, import batch, source row number, original row data, importing user, and timestamp.

The importer must be idempotent at the batch/row level so an accidental retry does not duplicate all inventory.

## 14. Search, filters, and screens

### Main screens

- Login
- Dashboard
- Inventory list
- Product catalog
- Asset detail and complete history
- Quantity-stock detail and ledger
- Receive stock
- Reserve stock
- Bulk transfer
- Assign to employee
- Deliver to customer
- Return and assessment
- Damage/loss/disposal
- Transactions and documents
- Locations
- Reports and exports
- Excel import
- User and permission administration
- Audit log

### Inventory filters

- Free-text search
- Brand
- Model
- SKU
- Type
- Vendor serial
- Status
- Country/site/floor/room/rack/shelf
- Project Reference
- Final Customer
- Arrival/removal/delivery date range
- Supplier
- Invoice number
- Duplicate serial flag

Lists must use server-side pagination, stable sorting, and indexed filters.

## 15. Reports

Initial reports:

- Current stock
- Stock by room/location
- Reserved stock
- Employee assignments
- Customer deliveries
- Stock by Project Reference
- Temporary assignments, without overdue automation
- Damaged assets
- Lost assets
- Disposed items, with particular use for HDD disposal records
- Complete asset movement history

All reports must honor user storage permissions. Exports use a new normalized format rather than reproducing the legacy Excel layout.

## 16. Optional configuration

- Low-stock threshold per quantity-tracked product
- Low-stock dashboard/report, disabled unless configured
- Temporary assignment flag and optional expected return date
- Future HDD sanitization/disposal fields

No notification service is required initially.

## 17. Non-functional requirements

### Performance

- Optimize for at least 100,000 inventory records without redesign.
- Typical filtered list response target: under 1 second on the internal network under normal load.
- Use database indexes for serial, normalized Brand/Model/SKU, Project Reference, Final Customer, status, location, and movement dates.
- Use pagination and avoid N+1 database queries.

### Security

- Hash passwords using the framework's current recommended password hasher.
- CSRF protection, secure cookies, session expiry, brute-force throttling, and password policy.
- Authorization on every read, write, report, export, and attachment download.
- Validate uploads and generated filenames.
- Do not expose stack traces or sensitive configuration.
- Keep secrets in environment variables or Docker secrets.

### Reliability

- PostgreSQL transactions around movement completion.
- Database constraints must prevent invalid quantities and broken relationships.
- Automated daily database and attachment backups should be documented even though no formal backup policy currently exists.
- Include restore instructions and verify the restore procedure before production launch.

### Usability

- Desktop-first responsive interface.
- Clear confirmation for irreversible business events.
- Accessible labels, keyboard navigation, and readable printable forms.
- Consistent date format configurable for the organization.

## 18. Suggested database entities

- User
- Role/Permission through framework authorization
- UserLocationAccess
- Country
- Site
- Floor
- StorageRoom
- RackCabinet
- ShelfBin
- Brand
- Product
- UnitAsset
- StockBalance
- InventoryTransaction
- InventoryTransactionLine
- AssetStatusHistory
- Attachment
- GeneratedDocument
- ImportBatch
- ImportRow
- AuditEvent

Important constraints:

- UnitAsset has an internal UUID primary key.
- VendorSerial is indexed but not unique.
- Unit-tracked transaction lines reference a UnitAsset and have quantity 1.
- Quantity-tracked lines reference a Product and quantity greater than 0.
- A completed movement is balanced between source/destination or explains an external receipt/removal.
- InventoryTransactionLine stores snapshots of display fields needed on historical documents.

## 19. API/service boundaries

Even with server-rendered pages, keep business logic in application services rather than views:

- Inventory receipt service
- Reservation service
- Transfer service
- Assignment/delivery service
- Return service
- Disposal/loss/damage service
- Correction/reversal service
- Document generation service
- Import validation/execution service
- Authorization scope service
- Reporting service

All UI actions and any future API must call the same services and validation rules.

## 20. Delivery phases

### Phase 1 — Foundation

- Repository, Docker Compose, PostgreSQL, authentication
- Roles and country/storage permissions
- Location hierarchy
- Product and asset models
- Audit framework

### Phase 2 — Inventory operations

- Receipts
- Serialized and quantity stock
- Duplicate serial workflow
- Reservations
- Bulk transfers
- Assignments and deliveries
- Partial returns
- Damage, loss, and disposal

### Phase 3 — Documents and reporting

- Replaceable printable PDF template
- Optional signed-form upload
- Dashboard, search, reports, and normalized exports

### Phase 4 — Migration and hardening

- Excel staging/import workflow
- Permission and security tests
- Performance/index review
- Backup and restore documentation
- User acceptance testing and production deployment

## 21. Minimum acceptance criteria

1. A Stock Manager can receive serialized and quantity-based stock.
2. Two different unit assets may share a vendor serial only after a visible acknowledgement.
3. A user cannot see records outside authorized storage scopes in any interface or export.
4. Multiple assets can be transferred between storage locations in one transaction.
5. Stock can be reserved against a manually entered Project Reference and Final Customer.
6. One assignment/delivery can contain multiple unit and quantity lines.
7. A partial return leaves outstanding items assigned/delivered.
8. Condition and accessories can be recorded at issue and return.
9. Removal Date is preserved for every event where inventory physically leaves storage.
10. Disposed HDDs remain searchable and appear in the disposed-items report.
11. Stock Managers complete movements without secondary approval.
12. Administrators can correct mistakes without erasing the original audit history.
13. Printable PDFs are immutable snapshots and optional signed copies can be uploaded.
14. The Excel importer previews errors and does not duplicate rows when safely retried.
15. All lists are paginated and remain responsive with at least 8,000 imported records.

## 22. Explicitly out of scope for the first release

- CRM integration or synchronization
- Automatic validation of Project References
- Customer/project master-data management
- Barcode/QR scanning and label printing
- Electronic signatures
- Mobile-specific application
- Offline operation
- Automated overdue notifications
- Mandatory minimum-stock alerts
- Customer addresses and contacts
- Secondary approval workflow

## 23. Implementation instructions for Codex and Claude Code

Use this document as the authoritative functional baseline.

1. Do not implement the entire system in one unreviewed change.
2. First produce the proposed repository structure, entity-relationship model, migration plan, permission matrix, and status-transition table.
3. Implement by the delivery phases in this specification.
4. Write tests alongside each workflow, especially authorization, duplicate serial acknowledgement, quantity integrity, partial returns, and corrections.
5. Use database constraints and transactional services for inventory integrity; do not rely only on browser validation.
6. Keep PDF presentation isolated in replaceable templates because the final company form will arrive later.
7. Preserve historical snapshots so later edits to products do not rewrite old forms.
8. Seed development data for all statuses, tracking types, and location levels.
9. Provide Docker-based local setup, database migration, test, backup, restore, and production deployment commands.
10. Stop and request a business decision if implementation exposes a conflict with this specification rather than silently inventing new workflow rules.

## 24. Remaining decisions that do not block initial development

- Final PDF assignment/delivery form design
- Whether low-stock thresholds will be enabled
- Exact HDD disposal/sanitization fields and certificate workflow
- Password complexity and session timeout values
- Corporate date format and timezone display
- Reverse proxy and TLS arrangement for the internal deployment

