from dataclasses import replace
from datetime import datetime

from provium_pipeline.dispatch_models import Dispatch, DispatchState


class DispatchStateConflictError(RuntimeError):
    """Raised when a compare-and-set dispatch transition observes another state."""


class InvalidDispatchTransitionError(ValueError):
    """Raised when a dispatch lifecycle edge is not legal."""


_ALLOWED_TARGETS: dict[DispatchState, frozenset[DispatchState]] = {
    DispatchState.CREATED: frozenset(
        (DispatchState.RUNNING, DispatchState.CANCELLED)
    ),
    DispatchState.RUNNING: frozenset(
        (
            DispatchState.SUCCEEDED,
            DispatchState.FAILED,
            DispatchState.CANCELLED,
        )
    ),
    DispatchState.SUCCEEDED: frozenset(),
    DispatchState.FAILED: frozenset(),
    DispatchState.CANCELLED: frozenset(),
}


def apply_dispatch_transition(
    dispatch: Dispatch,
    *,
    expected: DispatchState,
    target: DispatchState,
    transitioned_at: datetime,
) -> Dispatch:
    """Apply one legal compare-and-set transition to a durable dispatch."""
    if dispatch.state is not expected:
        raise DispatchStateConflictError(
            f"dispatch state conflict: expected {expected.value}, "
            f"found {dispatch.state.value}"
        )
    if target is expected:
        return dispatch
    if target not in _ALLOWED_TARGETS[expected]:
        raise InvalidDispatchTransitionError(
            f"invalid dispatch transition: {expected.value} -> {target.value}"
        )
    return replace(
        dispatch,
        state=target,
        terminal_at=transitioned_at if target.terminal else None,
    )


__all__ = [
    "DispatchStateConflictError",
    "InvalidDispatchTransitionError",
    "apply_dispatch_transition",
]
