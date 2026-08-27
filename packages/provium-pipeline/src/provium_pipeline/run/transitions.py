from dataclasses import replace

from provium_pipeline.run.models import PipelineRun, RunState


class RunStateConflictError(RuntimeError):
    """Raised when a compare-and-set run transition observes another state."""


class InvalidRunTransitionError(ValueError):
    """Raised when the requested run lifecycle transition is not legal."""


_ALLOWED_TARGETS: dict[RunState, frozenset[RunState]] = {
    RunState.PLANNED: frozenset({RunState.RUNNING, RunState.CANCELLED}),
    RunState.RUNNING: frozenset(
        {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


def apply_run_transition(
    run: PipelineRun,
    *,
    expected: RunState,
    target: RunState,
) -> PipelineRun:
    """Apply one legal compare-and-set transition to a durable run value."""

    if run.state is not expected:
        raise RunStateConflictError(
            f"run state conflict: expected {expected.value}, found {run.state.value}"
        )
    if target is expected:
        return run
    if target not in _ALLOWED_TARGETS[expected]:
        raise InvalidRunTransitionError(
            f"invalid run transition: {expected.value} -> {target.value}"
        )
    return replace(run, state=target)


__all__ = [
    "InvalidRunTransitionError",
    "RunStateConflictError",
    "apply_run_transition",
]
