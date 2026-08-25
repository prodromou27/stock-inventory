# Assumptions and Open Questions

Per spec §23.10: "Stop and request a business decision if implementation exposes a conflict with this specification
rather than silently inventing new workflow rules." Nothing below rises to a *blocker* — each has a documented
default that is consistent with every explicit rule in the spec and reversible without a schema rewrite if a
reviewer wants something different. Worth a skim before Phase 2/3 lock these in, since a couple are genuine
judgment calls.

## Judgment calls worth a second look

1. **`StockBalance` is per `(product, location)` only; Project Reference/Final Customer live on `StockReservation`
   and on transaction-line snapshots, not as dimensions of the balance itself.** Spec §6 lists "Project Reference"
   and "Final Customer" as fields on "Quantity stock," which could instead mean the spec wants a distinct balance
   row per project. This plan's design keeps balances simple and always reconcilable from the ledger at
   `(product, location)` granularity, and expresses project-scoped quantity as a reservation/allocation concept
   instead — satisfies "Stock by Project Reference" reporting (§15) via a query over reservations/transaction
   lines rather than over balances. If a reviewer wants true per-project balance buckets, this changes
   `StockBalance`'s unique constraint and adds real complexity to negative-balance prevention across buckets — worth
   confirming before Prompt 3.

2. **Location hierarchy modeled as one self-referential `Location` table (with `ltree`), not six separate tables**
   named in spec §18. Rationale and trade-off are in doc 02. This is the change most likely to surprise a reviewer
   expecting the literal six-entity list, even though it satisfies every stated rule about that hierarchy.

3. **Transaction numbers are sequential but not gapless** (a rolled-back transaction can consume a number with no
   document ever printed against it). Spec §10 says "unique sequential document number," which this satisfies;
   gapless numbering would require serializing all transaction writes, in tension with the concurrency/performance
   requirements in §17.

4. **"Delivered → Returned only when returns are allowed and recorded" (§8)** — the spec doesn't say what gates
   whether returns are allowed for a given delivery. Current default: returns are always *permitted* by the system
   (Stock Managers decide case-by-case whether a customer actually returns something); nothing blocks a return
   transaction against a `Delivered` line. If the intent was a per-product or per-transaction "returnable" flag,
   that's a small additive field, not a structural change — flagging so it isn't missed rather than silently
   assumed to be out of scope.

5. **Quantity-tracked "Mark damaged" decrements `on_hand_quantity` directly rather than moving stock into a
   separate "damaged quantity" bucket.** The spec's damage/loss/disposal narrative (§9) is written primarily
   around serialized assets (explicitly HDDs). If damaged consumables need their own trackable balance (e.g. "12
   toner cartridges damaged, awaiting disposal decision"), that's a `StockBalance`-shaped addition, not a rework —
   flagging since the current default would just remove the quantity from the visible balance immediately.

6. **Returned quantity-tracked stock has no per-unit "awaiting assessment" state** (unlike `UnitAsset`, which gets
   an explicit `Returned` status). Quantity by its nature can't hold a "some of this batch is unassessed" state
   without a separate holding balance. Current default: a quantity return immediately re-adds to `on_hand_quantity`
   at the receiving location, and the return-assessment step is *informational* (notes/condition on the return
   transaction line) rather than a required stateful gate for quantity items. Worth confirming this matches intent,
   since §8's "Returned... awaiting assessment" language reads as most natural for units.

7. **`condition` vocabulary** (`new, good, fair, damaged, unknown`) is not specified anywhere in the spec beyond the
   field existing. This is a cheap enum to change; flagged only so the seed data/tests aren't mistaken for a firm
   business decision.

8. **Tracking-method migration for a product with live stock** (§5 requires the *capability* to migrate an
   existing product between unit/quantity tracking, but not how in-flight `UnitAsset`s convert to a quantity
   balance or vice versa). Default: `migrate_tracking_method()` requires the product to be at zero on-hand
   quantity / have zero non-terminal `UnitAsset`s before converting, i.e., it's a "clean cutover" operation, not an
   automatic bulk-conversion of live stock. If real data needs a populated conversion, that's additional service
   logic layered onto the same operation, not a schema change.

9. **Resolved (Prompt 6, doc 07/09)**: the **PRODUCT DELIVERY / PRODUCT REMOVAL legacy import column** (§13) and
   the Delivery/Return/Removal date columns are *not* converted into movement transactions in v1 — the user chose
   "receipt-only" import over building a preview-time value-mapping UI (still §13's own "do not guess" instruction
   satisfied, just resolved by narrowing scope rather than by adding a mapping step). Every row becomes one
   receipt transaction; the legacy column and dates are preserved verbatim in the resulting record's `notes`.
   Further movement (delivery, return, disposal) is entered manually afterward through the normal interactive UI.

## Confirmed non-blocking (spec §24, restated for traceability — no action needed before Phase 1)

Final PDF template design, whether low-stock thresholds are enabled by default, exact HDD sanitization/certificate
fields, password complexity/session timeout exact values, corporate date format/timezone, and reverse-proxy/TLS
arrangement are all deferred exactly as spec §24 already states. This plan's defaults (doc 08) are conservative
placeholders, not decisions that need sign-off now.
