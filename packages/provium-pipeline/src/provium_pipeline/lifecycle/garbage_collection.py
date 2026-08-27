"""Two-step garbage-collection planning and application."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4


class RetentionLookup(Protocol):
    """Retention query needed to guard garbage collection."""

    def is_retained(self, artifact_identity: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class GarbageCollectionObject:
    """One physical location eligible for retention evaluation."""

    artifact_identity: str
    location_identity: str
    unreferenced_since: datetime


@dataclass(frozen=True, slots=True)
class GarbageCollectionPlan:
    """Frozen deterministic set of locations proposed for deletion."""

    identifier: str
    created_at: datetime
    candidates: tuple[GarbageCollectionObject, ...]


@dataclass(frozen=True, slots=True)
class GarbageCollectionResult:
    """Idempotent outcome of applying one frozen plan."""

    plan_identifier: str
    deleted: tuple[GarbageCollectionObject, ...]
    skipped: tuple[GarbageCollectionObject, ...]


class GarbageCollectionService:
    """Plan old unretained locations and safely revalidate them on apply."""

    def __init__(
        self,
        *,
        retention: RetentionLookup,
        delete: Callable[[str], None],
        plan_id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._retention = retention
        self._delete = delete
        self._plan_id_factory = plan_id_factory
        self._applied: dict[
            str, tuple[GarbageCollectionPlan, GarbageCollectionResult]
        ] = {}

    def plan(
        self,
        objects: Sequence[GarbageCollectionObject],
        *,
        now: datetime,
        grace_period: timedelta,
    ) -> GarbageCollectionPlan:
        """Freeze old candidates that have no retention references."""
        if grace_period < timedelta(0):
            raise ValueError("grace period cannot be negative")
        cutoff = now - grace_period
        candidates = (
            item
            for item in objects
            if item.unreferenced_since <= cutoff
            and not self._retention.is_retained(item.artifact_identity)
        )
        ordered = tuple(
            sorted(
                candidates,
                key=lambda item: (item.artifact_identity, item.location_identity),
            )
        )
        return GarbageCollectionPlan(self._plan_id_factory(), now, ordered)

    def apply(self, plan: GarbageCollectionPlan) -> GarbageCollectionResult:
        """Revalidate and delete a plan exactly once."""
        previous = self._applied.get(plan.identifier)
        if previous is not None:
            previous_plan, previous_result = previous
            if previous_plan != plan:
                raise RuntimeError("garbage-collection plan identifier collision")
            return previous_result
        deleted: list[GarbageCollectionObject] = []
        skipped: list[GarbageCollectionObject] = []
        for candidate in plan.candidates:
            if self._retention.is_retained(candidate.artifact_identity):
                skipped.append(candidate)
                continue
            self._delete(candidate.location_identity)
            deleted.append(candidate)
        result = GarbageCollectionResult(
            plan.identifier,
            tuple(deleted),
            tuple(skipped),
        )
        self._applied[plan.identifier] = (plan, result)
        return result


__all__ = [
    "GarbageCollectionObject",
    "GarbageCollectionPlan",
    "GarbageCollectionResult",
    "GarbageCollectionService",
    "RetentionLookup",
]
