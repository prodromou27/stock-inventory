# Status Transitions and Movement Rules

Covers spec §8–§9, Prompt 4. Applies to `UnitAsset.status`. Quantity-tracked inventory has no per-unit status —
its equivalent state is "available" vs. "reserved" quantity on `StockBalance`, moved by the same movement types
where applicable (see the Quantity-tracked column below).

## Status-transition table

| From ↓ / Movement → | Receipt | Transfer | Reserve | Release reservation | Assign | Deliver | Return | Return assessment | Mark damaged | Mark lost | Dispose | Admin correction | Reversal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| *(none — new asset)* | → In Stock | — | — | — | — | — | — | — | — | — | — | → any (Admin only) | — |
| **In Stock** | — | → In Stock (new location) | → Reserved | — | → Assigned | → Delivered | — | — | → Damaged | → Lost | → Disposed | → any | — |
| **Reserved** | — | → Reserved (new location) | — | → In Stock | → Assigned | → Delivered | — | — | — | → Lost | → Disposed | → any | — |
| **Assigned** | — | — | — | — | — | — | → Returned | — | → Damaged | → Lost | → Disposed | → any | — |
| **Delivered** | — | — | — | — | — | — | → Returned* | — | — | — | — | → any | — |
| **Returned** | — | — | — | — | — | — | — | → In Stock / Damaged / Disposed | — | — | — | → any | — |
| **Damaged** | — | — | — | — | — | — | — | — | — | — | → Disposed | → any | — |
| **Lost** | — | — | — | — | — | — | — | — | — | — | — | → In Stock (recovery) | — |
| **Disposed** | — | — | — | — | — | — | — | — | — | — | — | — | → prior status |

`*` Return from `Delivered` is only offered when the transaction/product configuration allows customer returns
(spec §8 — "Delivered → Returned only when returns are allowed and recorded"); see open question in doc 10 about
what gates this per spec's wording.

Every cell not listed is an invalid transition and the service layer rejects it with a clear error — never silently
coerced (spec §8, last line).

**Admin correction** is drawn separately from the table above: `AssetCorrectionService.correct_status()` may move an
asset from *any* status to *any* status, but only when called by an Administrator, and it always writes an audited
`InventoryTransaction(movement_type='correction')` plus an `AssetStatusHistory` row recording the forced transition
and the reason — it is not a silent field edit (spec §12).

**Reversal** creates a new transaction that undoes the *effect* of a specific prior transaction (moves the asset
back to its pre-transaction status/location) rather than deleting or editing that prior transaction. Available to
Administrators only, and only makes sense against the asset's *current* status if it still matches what the
reversed transaction produced (the service checks this and refuses a reversal that would silently skip an
intervening movement — it will tell the Administrator to use a correction instead).

## Movement type reference

| Movement type | Unit-tracked effect | Quantity-tracked effect | Removal Date set? | Who can perform |
|---|---|---|---|---|
| **Receipt** | Create `UnitAsset(status=In Stock)` | `StockBalance.on_hand_quantity += n` | No | Stock Manager, Administrator |
| **Location transfer** | `current_location` changes; `status` unchanged | Move `n` units of `on_hand_quantity` from source `StockBalance` to destination `StockBalance` (two ledger lines in one transaction) | No | Stock Manager, Administrator |
| **Reservation** | `status → Reserved` | Create/increment `StockReservation`; `StockBalance.reserved_quantity += n` | No | Stock Manager, Administrator |
| **Reservation release** | `status → In Stock` | `StockReservation.status → released`; `StockBalance.reserved_quantity -= n` | No | Stock Manager, Administrator |
| **Employee assignment** | `status → Assigned`, `current_location → NULL` (leaves storage) | `on_hand_quantity -= n` (and `reserved_quantity -= n` if consuming a reservation) | **Yes** | Stock Manager, Administrator |
| **Customer delivery** | `status → Delivered`, `current_location → NULL` | Same as assignment | **Yes** | Stock Manager, Administrator |
| **Return** | `status → Returned`, `current_location →` receiving location | `on_hand_quantity += n` at the returning location, pending assessment (see note) | No (prior Removal Date preserved in history) | Stock Manager, Administrator |
| **Return assessment** | `status → In Stock / Damaged / Disposed` | N/A (quantity returns don't carry a per-unit "awaiting assessment" state — see doc 10) | No | Stock Manager, Administrator |
| **Mark damaged** | `status → Damaged`, location unchanged | `on_hand_quantity -= n` moved to a damaged holding bucket, or a status flag — see doc 10 | No (location unchanged — spec §8 lists only assignments, deliveries, loss, and disposal) | Stock Manager, Administrator |
| **Mark lost** | `status → Lost`, `current_location → NULL` | `on_hand_quantity -= n` (write-off) | **Yes** | Stock Manager, Administrator |
| **Disposal** | `status → Disposed`, `current_location → NULL` | `on_hand_quantity -= n` (write-off) | **Yes** | Stock Manager, Administrator |
| **Admin correction** | Any field/status forced with reason | Adjust balance directly (may cross zero temporarily as an explicit, audited correction) | n/a | **Administrator only** |
| **Reversal** | Undo a specific transaction's effect | Undo a specific transaction's effect | n/a | **Administrator only** |

Quantity-tracked "Mark damaged" is intentionally left open in the table above — the spec describes damage/loss/
disposal primarily in terms of assets/HDDs (§9, "Disposal is especially important for HDD records") which are
always unit-tracked in the seed vocabulary (HDDs are serialized). Quantity-tracked damage is supported by the same
service with the balance simply decremented (no separate "damaged quantity" bucket) unless a reviewer wants a
distinct damaged-quantity balance — flagged in doc 10.

## Multi-line transactions

A single `InventoryTransaction` can contain any mix of unit lines and quantity lines (spec §9, "one
assignment/delivery can contain multiple unit and quantity lines," acceptance criterion §21.6). The service layer:

1. Validates every line's current status/scope *before* writing anything (fail the whole transaction if any line is
   invalid — no partial-success transactions except explicitly for partial returns, which are their own linked
   transaction against a subset of the original lines, not a partial-failure of one transaction).
2. Takes `select_for_update()` locks on every `StockBalance` row and every `UnitAsset` row involved, in a stable
   order (by primary key) to avoid deadlocks between concurrent multi-line transactions touching overlapping stock.
3. Writes all lines and denormalized state changes atomically.

## Partial returns

A partial return is a **new** `InventoryTransaction(movement_type='return', related_transaction_id=<original>)`
containing only the lines being returned now. Lines not included keep their `Assigned`/`Delivered` status
untouched — there is no partial mutation of the original transaction (spec §9, acceptance criterion §21.7).

## Removal Date

Stored as `InventoryTransaction.occurred_at` on the transaction whose `movement_type` is one of `assignment,
delivery, mark_lost, disposal` — there is no separate "Removal Date" column to keep in sync; it is *read* from
"the most recent transaction of one of those types for this asset that has not since been returned/recovered."
`UnitAsset.last_removal_date` (doc 02) is a denormalized copy of that value, written by the same service call, for
list/report performance — same same-transaction-denormalization pattern as `status`/`current_location`. A later
return does not clear `last_removal_date`; it is overwritten only when a *new* removal-causing movement occurs,
matching "If an asset returns, the prior removal date remains in history" (§8).
