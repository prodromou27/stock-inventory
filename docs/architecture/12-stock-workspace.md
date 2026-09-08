# Stock workspace simplification

Implemented following the user's September 2026 request to simplify receiving,
room stock, delivery, and personal dashboards.

- New users see four dashboard totals: units in stock, quantity on hand, assigned,
  and delivered. Existing personal card selections remain unchanged. Every user
  can choose their own visible totals through Customize dashboard.
- Room links filter the same authorized inventory querysets used by lists and
  exports. Individual items and quantity stock remain separate representations;
  the selected location is retained when switching between them.
- `in_storage=1` means individual items with a current location, including reserved
  or damaged items physically present. For quantities it means a positive on-hand
  balance. Available quantity continues to exclude reservations.
- Grid filters support type/product_type, condition, and named location ancestors.
  Dashboard and product links seed the grid's initial filters and exports. Explicit
  URL filters take precedence over a saved default view.
- Receive stock uses the existing multi-line receipt service. The older receiving
  forms remain available under Other receiving options.
- Completing delivery from the form also requests a printable document. Stock
  movement commits first; PDF generation follows through the existing document
  service. If rendering/storage fails, the delivery remains complete and the user
  is sent to its transaction to retry document generation without moving stock again.
- All stock changes, reservation checks, project snapshots, and document history
  retain their existing service and authorization rules. This change introduces no
  tracking conversion or new ledger mutation.

The visual refresh uses the existing Tailwind build and locally bundled assets.
JavaScript-created component classes are included in Tailwind's content scan.
