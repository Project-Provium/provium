from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from provium_pipeline.attempts import RetryPolicy
from provium_pipeline.identifiers import DispatchId, RunId, TaskId


class DependencyPolicy(StrEnum):
    """How a dispatch treats required upstream tasks outside its selection."""

    SELECTED_ONLY = "selected-only"
    INCLUDE_MISSING_UPSTREAM = "include-missing-upstream"


class DispatchState(StrEnum):
    """Durable lifecycle state for one dispatch."""

    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            DispatchState.SUCCEEDED,
            DispatchState.FAILED,
            DispatchState.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class TaskSelection:
    """Canonical task filters shared by run and dispatch commands."""

    all: bool = False
    all_remaining: bool = False
    labels: tuple[str, ...] = ()
    nodes: tuple[str, ...] = ()
    only_failed: bool = False
    procedures: tuple[str, ...] = ()
    records: tuple[str, ...] = ()
    only_incomplete: bool = True

    def __post_init__(self) -> None:
        if self.all and self.all_remaining:
            raise ValueError("all and all_remaining are mutually exclusive")
        for field in ("labels", "nodes", "procedures", "records"):
            values = getattr(self, field)
            object.__setattr__(self, field, tuple(sorted(set(values))))


@dataclass(frozen=True, slots=True)
class Dispatch:
    """Immutable durable dispatch snapshot."""

    identifier: DispatchId
    run_identifier: RunId
    selection: TaskSelection
    task_identifiers: tuple[TaskId, ...]
    dependency_policy: DependencyPolicy
    retry_policy: RetryPolicy
    state: DispatchState
    created_at: datetime
    terminal_at: datetime | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "task_identifiers",
            tuple(sorted(set(self.task_identifiers), key=str)),
        )
        _require_aware(self.created_at, field="created_at")
        if self.terminal_at is not None:
            _require_aware(self.terminal_at, field="terminal_at")
        if self.state.terminal and self.terminal_at is None:
            raise ValueError("terminal dispatch requires terminal_at")
        if not self.state.terminal and self.terminal_at is not None:
            raise ValueError("non-terminal dispatch cannot have terminal_at")
        if self.idempotency_key == "":
            raise ValueError("idempotency_key cannot be empty")


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


__all__ = [
    "DependencyPolicy",
    "Dispatch",
    "DispatchState",
    "TaskSelection",
]
