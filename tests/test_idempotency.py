import pytest

from apps.core.idempotency import (
    claim_submission_token,
    new_submission_token,
    release_submission_token,
)
from apps.core.models import SubmissionClaim


@pytest.mark.django_db(transaction=True)
def test_submission_token_is_claimed_once_in_the_database():
    token = new_submission_token()

    assert claim_submission_token(token) is True
    assert SubmissionClaim.objects.filter(token=token).exists()
    assert claim_submission_token(token) is False


@pytest.mark.django_db
def test_failed_submission_claim_can_be_released_for_a_corrected_retry():
    token = new_submission_token()

    assert claim_submission_token(token) is True
    release_submission_token(token)

    assert claim_submission_token(token) is True


@pytest.mark.django_db
def test_invalid_submission_token_is_rejected():
    assert claim_submission_token("not-a-uuid") is False
