from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provium_pipeline.execution.attempts import (
    AttemptLeaseManager,
    LeaseConflictError,
    RetryPolicy,
)
from provium_pipeline.identifiers import TaskId
from provium_pipeline.run.models import TaskState

_NOW = datetime(2026, 8, 23, tzinfo=UTC)
_TASK = TaskId(UUID("00000000-0000-0000-0000-000000000001"))


def test_claim_renews_and_releases_with_fencing_token() -> None:
    manager = AttemptLeaseManager()
    lease = manager.claim(
        _TASK,
        state=TaskState.READY,
        worker_identity="worker-1",
        token="token-1",
        now=_NOW,
        ttl=timedelta(seconds=30),
    )

    assert lease.attempt_number == 1
    assert lease.expires_at == _NOW + timedelta(seconds=30)
    renewed = manager.renew(
        _TASK,
        token="token-1",
        now=_NOW + timedelta(seconds=10),
        ttl=timedelta(seconds=30),
    )
    assert renewed.heartbeat_at == _NOW + timedelta(seconds=10)
    assert renewed.expires_at == _NOW + timedelta(seconds=40)
    released = manager.release(_TASK, token="token-1", ended_at=renewed.expires_at)
    assert released.ended_at == renewed.expires_at


def test_claim_rejects_nonready_duplicate_and_nonpositive_ttl() -> None:
    manager = AttemptLeaseManager()
    with pytest.raises(ValueError, match="claimable"):
        manager.claim(
            _TASK,
            state=TaskState.BLOCKED,
            worker_identity="worker",
            token="token",
            now=_NOW,
            ttl=timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="positive"):
        manager.claim(
            _TASK,
            state=TaskState.READY,
            worker_identity="worker",
            token="token",
            now=_NOW,
            ttl=timedelta(0),
        )

    manager.claim(
        _TASK,
        state=TaskState.READY,
        worker_identity="worker",
        token="token",
        now=_NOW,
        ttl=timedelta(seconds=1),
    )
    with pytest.raises(LeaseConflictError):
        manager.claim(
            _TASK,
            state=TaskState.READY,
            worker_identity="other",
            token="other",
            now=_NOW,
            ttl=timedelta(seconds=1),
        )


def test_wrong_or_expired_fencing_token_cannot_mutate_lease() -> None:
    manager = AttemptLeaseManager()
    manager.claim(
        _TASK,
        state=TaskState.READY,
        worker_identity="worker",
        token="token",
        now=_NOW,
        ttl=timedelta(seconds=10),
    )

    with pytest.raises(LeaseConflictError):
        manager.renew(
            _TASK,
            token="wrong",
            now=_NOW,
            ttl=timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="positive"):
        manager.renew(
            _TASK,
            token="token",
            now=_NOW,
            ttl=timedelta(0),
        )
    with pytest.raises(LeaseConflictError, match="expired"):
        manager.renew(
            _TASK,
            token="token",
            now=_NOW + timedelta(seconds=11),
            ttl=timedelta(seconds=1),
        )
    with pytest.raises(LeaseConflictError):
        manager.release(_TASK, token="wrong", ended_at=_NOW)


def test_expired_lease_is_recovered_and_next_claim_increments_attempt() -> None:
    manager = AttemptLeaseManager()
    first = manager.claim(
        _TASK,
        state=TaskState.READY,
        worker_identity="worker",
        token="first",
        now=_NOW,
        ttl=timedelta(seconds=10),
    )

    assert manager.recover_expired(_NOW + timedelta(seconds=11)) == (first,)
    second = manager.claim(
        _TASK,
        state=TaskState.RETRY_WAIT,
        worker_identity="worker",
        token="second",
        now=_NOW + timedelta(seconds=11),
        ttl=timedelta(seconds=10),
    )
    assert second.attempt_number == 2


def test_retry_policy_uses_capped_exponential_backoff_and_exhaustion() -> None:
    policy = RetryPolicy(
        max_attempts=3,
        initial_backoff=timedelta(seconds=2),
        multiplier=3,
        max_backoff=timedelta(seconds=5),
    )

    first = policy.after_failure(attempt_number=1, retryable=True, now=_NOW)
    second = policy.after_failure(attempt_number=2, retryable=True, now=_NOW)
    exhausted = policy.after_failure(attempt_number=3, retryable=True, now=_NOW)
    permanent = policy.after_failure(attempt_number=1, retryable=False, now=_NOW)

    assert first.state is TaskState.RETRY_WAIT
    assert first.eligible_at == _NOW + timedelta(seconds=2)
    assert not first.is_eligible(_NOW + timedelta(seconds=1))
    assert first.is_eligible(_NOW + timedelta(seconds=2))
    assert second.eligible_at == _NOW + timedelta(seconds=5)
    assert exhausted.state is TaskState.FAILED
    assert exhausted.eligible_at is None
    assert not exhausted.is_eligible(_NOW + timedelta(days=1))
    assert permanent.state is TaskState.FAILED


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_retry_policy_requires_positive_attempt_count(max_attempts: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        RetryPolicy(max_attempts=max_attempts)


def test_retry_policy_rejects_nonpositive_attempt_number() -> None:
    policy = RetryPolicy(max_attempts=2)

    with pytest.raises(ValueError, match="positive"):
        policy.after_failure(attempt_number=0, retryable=True, now=_NOW)


def test_retry_policy_rejects_negative_backoff_and_decreasing_multiplier() -> None:
    with pytest.raises(ValueError, match="negative"):
        RetryPolicy(max_attempts=1, initial_backoff=timedelta(seconds=-1))
    with pytest.raises(ValueError, match="negative"):
        RetryPolicy(max_attempts=1, max_backoff=timedelta(seconds=-1))
    with pytest.raises(ValueError, match="at least one"):
        RetryPolicy(max_attempts=1, multiplier=0.5)
