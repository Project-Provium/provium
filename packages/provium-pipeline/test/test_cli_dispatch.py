from argparse import Namespace
from datetime import UTC, datetime
from typing import cast

from provium_pipeline.cli import LocalCLIBackend
from provium_pipeline.dispatch_codec import dispatch_document
from provium_pipeline.dispatch_models import DispatchState
from provium_pipeline.dispatch_store import InMemoryDispatchStore
from provium_pipeline.execution_store import InMemoryExecutionStore
from provium_pipeline.run_query import RunLookup
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


def test_local_cli_backend_reports_unsupported_dispatch_action() -> None:
    dispatches = InMemoryDispatchStore()
    dispatch = dispatches.create(dispatch_for_state(DispatchState.RUNNING))
    backend = _backend(dispatches)

    result = backend.execute(
        "dispatch",
        "wait",
        Namespace(dispatch_id=str(dispatch.identifier)),
    )

    assert result.exit_code == 2
    assert result.data == {"action": "wait", "group": "dispatch"}
    assert result.message == "local backend does not support dispatch wait yet"
