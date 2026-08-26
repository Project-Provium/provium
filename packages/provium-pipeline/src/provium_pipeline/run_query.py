"""Read models for inspecting pipeline runs without mutating execution state."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast


class _State(Protocol):
    value: str


class _Run(Protocol):
    identifier: object
    state: _State
    inputs: object
    expected_outputs: tuple[object, ...]


class _Task(Protocol):
    identifier: object
    record_key: object
    node_identifier: str
    state: _State
    expected_output_fields: tuple[str, ...]


class RunLookup(Protocol):
    def get_run(self, identifier: object) -> _Run: ...

    def list_tasks(self, identifier: object) -> tuple[_Task, ...]: ...


ObservationProvider = Callable[[object], Sequence[object]]
CacheDispositionProvider = Callable[[object], Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class RunTaskView:
    task_id: str
    record_key: str
    node_id: str
    status: str
    expected_output_fields: tuple[str, ...]
    cache_disposition: str | None


@dataclass(frozen=True, slots=True)
class RunView:
    run_id: str
    status: str
    status_counts: Mapping[str, int]
    tasks: tuple[RunTaskView, ...]
    attempts: tuple[object, ...]
    cache_dispositions: Mapping[str, str]
    inputs: object
    expected_outputs: tuple[object, ...]
    outputs: tuple[object, ...]
    audit_timeline: tuple[object, ...]


class RunQueryService:
    """Compose an authoritative run view from narrow read-only providers."""

    def __init__(
        self,
        store: RunLookup,
        *,
        attempts: ObservationProvider | None = None,
        cache_dispositions: CacheDispositionProvider | None = None,
        outputs: ObservationProvider | None = None,
        audit_events: ObservationProvider | None = None,
    ) -> None:
        self._store = store
        self._attempts = attempts or _empty_observations
        self._cache_dispositions = cache_dispositions or _empty_dispositions
        self._outputs = outputs or _empty_observations
        self._audit_events = audit_events or _empty_observations

    def get(self, run_id: object) -> RunView:
        run = self._store.get_run(run_id)
        tasks = sorted(
            self._store.list_tasks(run_id),
            key=lambda task: str(task.identifier),
        )
        dispositions = dict(sorted(self._cache_dispositions(run_id).items()))
        return RunView(
            run_id=str(run.identifier),
            status=run.state.value,
            status_counts=dict(
                sorted(Counter(task.state.value for task in tasks).items())
            ),
            tasks=_task_views(tasks, dispositions),
            attempts=tuple(self._attempts(run_id)),
            cache_dispositions=dispositions,
            inputs=_ordered_inputs(run.inputs),
            expected_outputs=run.expected_outputs,
            outputs=tuple(self._outputs(run_id)),
            audit_timeline=tuple(
                sorted(self._audit_events(run_id), key=_sequence_key)
            ),
        )


def _task_views(
    tasks: Sequence[_Task],
    dispositions: Mapping[str, str],
) -> tuple[RunTaskView, ...]:
    return tuple(
        RunTaskView(
            task_id=str(task.identifier),
            record_key=str(task.record_key),
            node_id=task.node_identifier,
            status=task.state.value,
            expected_output_fields=task.expected_output_fields,
            cache_disposition=dispositions.get(str(task.identifier)),
        )
        for task in tasks
    )


def _ordered_inputs(inputs: object) -> object:
    if not isinstance(inputs, Mapping):
        return inputs
    input_mapping = cast(Mapping[object, object], inputs)
    return dict(sorted(input_mapping.items(), key=lambda item: str(item[0])))


def _sequence_key(event: object) -> int:
    if isinstance(event, Mapping):
        event_mapping = cast(Mapping[object, object], event)
        sequence = event_mapping.get("sequence", 0)
        if isinstance(sequence, int):
            return sequence
    return 0


def _empty_observations(run_id: object) -> tuple[object, ...]:
    return ()


def _empty_dispositions(run_id: object) -> Mapping[str, str]:
    return {}


__all__ = ["RunLookup", "RunQueryService", "RunTaskView", "RunView"]
