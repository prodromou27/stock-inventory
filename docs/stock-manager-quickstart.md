# Stock Manager Quick-Start Guide

For day-to-day inventory operations. Every screen here is reachable from the top navigation once you're logged
in — you'll only see the ones your role and location access allow.

## What you can see

You only see products, assets, balances, transactions, and reports for the storage locations an Administrator has
granted you access to (**Manage Access**, Administrator-only) — including their sub-locations. If a screen looks
emptier than you expect, that's most likely a location-access question for an Administrator, not a bug.

## Receiving stock

**Movements → Receive Stock.** Pick the product (or create a new Brand/Model/Type if it doesn't exist yet — you'll
be asked to confirm if it looks like a near-duplicate of an existing product), the receiving location, and either
a vendor serial (serialized products — one receipt per physical unit) or a quantity (quantity-tracked products,
like consumables). If the serial number matches one already in the system, you'll be shown the match and asked to
confirm before it's allowed through — this is deliberate, not a bug, in case the same serial was mistakenly entered
twice or a legitimately reused/refurbished serial needs a second entry.

## Moving stock around

- **Bulk transfer** — move multiple assets and/or a quantity line to a different location in one transaction.
- **Reserve stock** — hold assets or a quantity against a Project Reference and Final Customer you type in (these
  aren't validated against any external system — enter them exactly as your project/customer tracking expects).
- **Assign to employee** / **Deliver to customer** — one transaction can include any number of serialized units
  plus one quantity line. Mark it as a temporary assignment with an expected return date if it's not permanent.
- **Return & assessment** — record what physically came back (a return can be partial — whatever isn't returned
  stays assigned/delivered) and separately assess its condition once it's back in hand.
- **Mark damaged / Mark lost / Dispose** — status changes for stock that's no longer usable. A disposed HDD stays
  searchable and shows up in the disposed-items report indefinitely — nothing here is ever hard-deleted.

Every one of these completes immediately once you submit it — there's no secondary approval step to wait on.

## Printable documents

After completing an assignment or delivery, its detail page has a **Generate document** button producing a PDF
snapshot of exactly what was on that transaction at that moment — renaming the product or editing the transaction
later never changes a document already generated. If you need to reprint (e.g. a form got smudged), use
**Regenerate** — it creates a new numbered document, linked to the one it replaces, without touching the original.
You can upload a scanned signed copy as an attachment once you have one back.

## Finding things

Every list (Assets, Stock Balances, Transactions) has a filter bar — free-text search plus specific filters for
brand, model, SKU, type, vendor serial, status, project reference, final customer, supplier, and location
(picking a location includes everything under it). Every list also has a CSV export link that respects whatever
filters you've applied. Lists are paginated — even a very large inventory stays responsive.

## Corrections

If you enter something wrong, you can't edit or delete a completed transaction yourself — that's by design, so the
history stays trustworthy. Ask an Administrator to apply a correction or, if the whole transaction needs undoing,
a reversal; either way, the original record stays exactly as it was, and the correction/reversal is its own new,
visible entry in the asset or balance's history.
