# Individual asset detail corrections

Requested addition: authorized Administrators and Stock Managers can edit an
asset after receiving it, including its optional individual name, serial,
Brand, Model, SKU, and descriptive details.

`services/asset_editing.py` locks and authorizes the asset before saving and
records old/new values atomically. Brand/Model/SKU corrections resolve a
catalog product for this asset only, without editing the shared product.
The existing tracking method and category are preserved. Historical movement
lines and document snapshots are never modified. The optional asset name is
searchable in inventory and global search; it is not a replacement for the
catalog Brand/Model or a generated asset tag.

Serial edits reuse receipt normalization, advisory locking, duplicate checks,
and scoped match disclosure. Empty serials remain supported. Duplicate
acknowledgement is recorded in the audit log without modifying a past receipt.

Location and status are not descriptive edits: the editor links to the
existing transfer workflow, and items outside storage use returns. Neither
the editor nor its service accepts direct lifecycle changes. Read-only users
and out-of-scope users cannot access the editor or save changes.
