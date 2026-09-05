"""Cross-worker idempotency for stock-changing browser submissions."""

import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import SubmissionClaim

_TOKEN_TIMEOUT_SECONDS = 300


def new_submission_token():
    return str(uuid.uuid4())


def claim_submission_token(token):
    """Atomically claim a token in PostgreSQL; return False if already used."""
    if not token:
        return True
    try:
        parsed_token = uuid.UUID(str(token))
    except (TypeError, ValueError, AttributeError):
        return False

    cutoff = timezone.now() - timedelta(seconds=_TOKEN_TIMEOUT_SECONDS)
    SubmissionClaim.objects.filter(claimed_at__lt=cutoff).delete()
    try:
        with transaction.atomic():
            SubmissionClaim.objects.create(token=parsed_token)
    except IntegrityError:
        return False
    return True


def release_submission_token(token):
    """Release a claim when downstream validation prevents a stock write."""
    try:
        parsed_token = uuid.UUID(str(token))
    except (TypeError, ValueError, AttributeError):
        return
    SubmissionClaim.objects.filter(token=parsed_token).delete()
