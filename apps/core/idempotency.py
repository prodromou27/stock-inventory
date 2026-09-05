"""Prevents a double-click or a back-button resubmission from creating stock
twice — every form that mutates stock (Add Stock, and the bulk-operation
forms in a later phase) renders a fresh, single-use token; the view rejects
a POST that reuses one already spent.

Uses Django's cache framework rather than a new model/table — this is a
short-lived (5 minute), fire-and-forget marker, not data anyone ever reads
back, so a table + migration would be more machinery than the problem
needs. `cache.add()` is atomic (returns False if the key already exists),
which is what makes this safe against two near-simultaneous requests
carrying the same token, not just a plain get-then-set race.

Note: with the default LocMemCache (this project's setting, unless a
shared backend like Redis is configured for production), this only
protects within one worker process — a real multi-worker deployment needs
a shared cache backend for the same guarantee across processes. That's an
existing deployment-configuration concern, not something this module can
fix on its own.
"""

import uuid

from django.core.cache import cache

_TOKEN_TIMEOUT_SECONDS = 300


def new_submission_token():
    return str(uuid.uuid4())


def claim_submission_token(token):
    """True if this POST should proceed — either `token` hasn't been seen
    before (and is now marked claimed), or there's no token at all, which
    just means the caller isn't using this optional protection (a script,
    an older cached page, a test posting directly) rather than a detected
    resubmission. False only when the same non-blank token is claimed a
    second time — a real double-click or back-button resubmission of a
    page that *did* render one.
    """
    if not token:
        return True
    return cache.add(f"submission-token:{token}", True, timeout=_TOKEN_TIMEOUT_SECONDS)
