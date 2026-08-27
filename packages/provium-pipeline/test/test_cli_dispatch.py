from argparse import Namespace
from datetime import UTC, datetime
from typing import cast

import pytest

from provium_pipeline.cli import LocalCLIBackend
from provium_pipeline.dispatch.codec import dispatch_document
from provium_pipeline.dispatch.models import DispatchState
from provium_pipeline.dispatch.store import InMemoryDispatchStore
from provium_pipeline.dispatch.transitions import InvalidDispatchTransitionError
from provium_pipeline.execution_store import InMemoryExecutionStore
from provium_pipeline.run.query import RunLookup
from test.test_dispatch_transitions import dispatch_for_state


def _backend(dispatches: InMemoryDispatchStore) -> LocalCLIBackend:
    runs = InMemoryExecutionStore(clock=lambda: datetime.now(UTC))
    return LocalCLIBackend(cast(RunLookup, runs), dispatches=dispatches)


def test_local_cli_backend_shows_canonical_dispatch() -> None:
    dispatches = InMemoryDispatchStore()
    dispatch = dispatches.create(dispatch_for_state(DispatchState.RUNNING))
    backend = _backend(dispatches)

    result = backend.execute(
        "dispatch",
        "show",
        Namespace(dispatch_id=str(dispatch.identifier)),
    )

    assert result.exit_code == 0
    assert result.message is None
    assert result.data == {"dispatch": dispatch_document(dispatch)["dispatch"]}


def test_local_cli_backend_reports_unsupported_dispatch_retry_action() -> None:
    dispatches = InMemoryDispatchStore()
    dispatch = dispatches.create(dispatch_for_state(DispatchState.RUNNING))
    backend = _backend(dispatches)

    result = backend.execute(
        "dispatch",
        "retry",
        Namespace(dispatch_id=str(dispatch.identifier)),
    )

    assert result.exit_code == 2
    assert result.data == {"action": "retry", "group": "dispatch"}
    assert result.message == "local backend does not support dispatch retry yet"


def test_local_cli_backend_cancels_dispatch_idempotently() -> None:
    dispatches = InMemoryDispatchStore()
    dispatch = dispatches.create(dispatch_for_state(DispatchState.RUNNING))
    backend = _backend(dispatches)
    arguments = Namespace(dispatch_id=str(dispatch.identifier))

    first = backend.execute("dispatch", "cancel", arguments)
    second = backend.execute("dispatch", "cancel", arguments)
    cancelled = dispatches.get(dispatch.identifier)

    assert cancelled.state is DispatchState.CANCELLED
    assert first.data == {"dispatch": dispatch_document(cancelled)["dispatch"]}
    assert second.data == first.data


def test_local_cli_backend_rejects_completed_dispatch_cancellation() -> None:
    dispatches = InMemoryDispatchStore()
    dispatch = dispatches.create(dispatch_for_state(DispatchState.SUCCEEDED))
    backend = _backend(dispatches)

    with pytest.raises(InvalidDispatchTransitionError):
        backend.execute(
            "dispatch",
            "cancel",
            Namespace(dispatch_id=str(dispatch.identifier)),
        )
