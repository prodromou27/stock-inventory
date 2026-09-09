# Internal use (approved workflow extension)

User-approved addition: In Use distinguishes installed internal infrastructure
from equipment assigned to employees. Product examples are guidance, not
category restrictions. Quantity balances keep their existing workflows.

- Put in use: Internal, In Stock or Returned unit assets become In Use, leave storage,
  record removal date, and retain installation details in notes and the ledger.
- Reserved assets must first be released; Customer-purpose stock must first
  be reclassified using the existing audited workflow.
- Return from internal use: In Use assets become In Stock in an authorized,
  active room/floor. Record removal notes; original installation history stays.
- To damage, lose, dispose of, or issue such equipment, return it from use
  first, then use the relevant workflow. Administrator corrections/reversals
  remain available without editing historical ledger rows.
- Permissions: Administrator and Stock Manager, checked against each asset's
  current or last storage scope and the return destination. Read-only users
  can view the scoped inventory status, but cannot execute either movement.
- No employee/customer custody is implied. In Use is valid with a null current
  storage location, so location/custodian data-quality checks must not flag it.
- Both movements lock assets in UUID order and write ledger, status history,
  notes audit, and movement audit atomically. Normal grid filtering/exports and
  saved report status choices expose the new state.

The importer must not infer In Use, Delivered, or Assigned from an empty
location alone; the ambiguous-import decision remains separate.
