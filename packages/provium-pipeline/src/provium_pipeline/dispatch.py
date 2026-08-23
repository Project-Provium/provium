"""Pure deterministic selection of tasks for local dispatch."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from provium_pipeline.identifiers import InputRecordKey, TaskId
from provium_pipeline.run_models import TaskState


class DispatchCandidate(Protocol):
    """The task fields required by dispatch selection."""

    @property
    def identifier(self) -> TaskId: ...

    @property
    def node_identifier(self) -> str: ...

    @property
    def record_key(self) -> InputRecordKey: ...

    @property
    def procedure_identifier(self) -> str: ...

    @property
    def state(self) -> TaskState: ...


@dataclass(frozen=True, slots=True, init=False)
class DispatchLimits:
    """Immutable global, procedure, and concurrency-group capacities."""

    max_workers: int
    procedure_limits: Mapping[str, int]
    procedure_groups: Mapping[str, str]
    group_limits: Mapping[str, int]

    def __init__(
        self,
        *,
        max_workers: int,
        procedure_limits: Mapping[str, int] = MappingProxyType({}),
        procedure_groups: Mapping[str, str] = MappingProxyType({}),
        group_limits: Mapping[str, int] = MappingProxyType({}),
    ) -> None:
        limits = (max_workers, *procedure_limits.values(), *group_limits.values())
        if any(limit <= 0 for limit in limits):
            raise ValueError("dispatch limits must be positive")
        object.__setattr__(self, "max_workers", max_workers)
        object.__setattr__(
            self, "procedure_limits", MappingProxyType(dict(procedure_limits))
        )
        object.__setattr__(
            self, "procedure_groups", MappingProxyType(dict(procedure_groups))
        )
        object.__setattr__(self, "group_limits", MappingProxyType(dict(group_limits)))


def select_ready_tasks[Task: DispatchCandidate](
    tasks: Sequence[Task],
    *,
    active_tasks: Sequence[DispatchCandidate],
    node_order: Mapping[str, int],
    limits: DispatchLimits,
) -> tuple[Task, ...]:
    """Select ready tasks in stable order without exceeding any capacity."""
    available_workers = limits.max_workers - len(active_tasks)
    if available_workers <= 0:
        return ()

    procedure_counts = Counter(task.procedure_identifier for task in active_tasks)
    group_counts = Counter(
        group
        for task in active_tasks
        if (group := limits.procedure_groups.get(task.procedure_identifier)) is not None
    )
    ready = sorted(
        (task for task in tasks if task.state is TaskState.READY),
        key=lambda task: (
            node_order[task.node_identifier],
            str(task.record_key),
            str(task.identifier),
        ),
    )
    selected: list[Task] = []
    for task in ready:
        if len(selected) >= available_workers:
            break
        procedure_limit = limits.procedure_limits.get(task.procedure_identifier)
        if (
            procedure_limit is not None
            and procedure_counts[task.procedure_identifier] >= procedure_limit
        ):
            continue
        group = limits.procedure_groups.get(task.procedure_identifier)
        if group is not None:
            group_limit = limits.group_limits.get(group)
            if group_limit is not None and group_counts[group] >= group_limit:
                continue
        selected.append(task)
        procedure_counts[task.procedure_identifier] += 1
        if group is not None:
            group_counts[group] += 1
    return tuple(selected)
