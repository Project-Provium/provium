from __future__ import annotations

from uuid import UUID

import pytest

from provium import CancellationToken
from provium_pipeline.attempts import LeaseConflictError
from provium_pipeline.cancellation import (
    DispatchCancellationController,
    cancelled_task_state,
)
from provium_pipeline.identifiers import TaskId
from provium_pipeline.run_models import TaskState

_TASK = TaskId(UUID("00000000-0000-0000-0000-000000000001"))


@pytest.mark.parametrize(
    "state",
    [
        TaskState.BLOCKED,
        TaskState.READY,
        TaskState.WAITING_ON_COMPUTATION,
        TaskState.RETRY_WAIT,
        TaskState.LEASED,
    ],
)
def test_unfinished_task_states_become_cancelled(state: TaskState) -> None:
    assert cancelled_task_state(state) is TaskState.CANCELLED


@pytest.mark.parametrize("state", [TaskState.SUCCEEDED, TaskState.REUSED])
def test_satisfied_outputs_remain_available_after_cancellation(
    state: TaskState,
) -> None:
    assert cancelled_task_state(state) is state


def test_cancellation_stops_claims_and_signals_all_active_attempts() -> None:
    controller = DispatchCancellationController()
    first = CancellationToken()
    second = CancellationToken()
    other_task = TaskId(UUID("00000000-0000-0000-0000-000000000002"))
    controller.register(_TASK, lease_token="lease-1", cancellation=first)
    controller.register(other_task, lease_token="lease-2", cancellation=second)

    assert controller.can_claim
    assert controller.request_cancellation()
    assert not controller.can_claim
    assert first.cancelled
    assert second.cancelled
    assert not controller.request_cancellation()


def test_attempt_registered_after_request_is_cancelled_immediately() -> None:
    controller = DispatchCancellationController()
    controller.request_cancellation()
    cancellation = CancellationToken()

    controller.register(_TASK, lease_token="lease", cancellation=cancellation)

    assert cancellation.cancelled


def test_active_attempt_unregister_is_fenced_by_lease_token() -> None:
    controller = DispatchCancellationController()
    cancellation = CancellationToken()
    controller.register(_TASK, lease_token="lease", cancellation=cancellation)

    with pytest.raises(LeaseConflictError):
        controller.unregister(_TASK, lease_token="wrong")

    assert controller.unregister(_TASK, lease_token="lease") is cancellation
    with pytest.raises(LeaseConflictError):
        controller.unregister(_TASK, lease_token="lease")


def test_duplicate_registration_is_rejected() -> None:
    controller = DispatchCancellationController()
    controller.register(
        _TASK, lease_token="lease", cancellation=CancellationToken()
    )

    with pytest.raises(LeaseConflictError):
        controller.register(
            _TASK, lease_token="other", cancellation=CancellationToken()
        )
