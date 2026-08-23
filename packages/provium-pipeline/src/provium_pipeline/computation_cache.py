"""Thread-safe in-memory computation cache and single-flight reservations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Protocol

from provium_pipeline.identifiers import ComputationKey, TaskId


class InvalidCacheEntryError(ValueError):
    """A cache entry cannot safely be reused."""


class ReservationOwnerError(RuntimeError):
    """A non-owner attempted to finish a single-flight reservation."""


class _ArtifactAvailability(Protocol):
    def get_artifact(self, artifact_identity: str) -> object: ...

    def get_active_locations(self, artifact_identity: str) -> tuple[object, ...]: ...


@dataclass(frozen=True, slots=True)
class CachedOutput:
    """One ordered output identity, including explicit optional absence."""

    field: str
    artifact_identity: str | None


@dataclass(frozen=True, slots=True, init=False)
class ComputationCacheEntry:
    """Reusable outputs for a completed semantic computation."""

    key: ComputationKey
    outputs: tuple[CachedOutput, ...]
    created_at: datetime

    def __init__(
        self,
        *,
        key: ComputationKey,
        outputs: Sequence[CachedOutput],
        created_at: datetime,
    ) -> None:
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "outputs", tuple(outputs))
        object.__setattr__(self, "created_at", created_at)


class CacheReservationDisposition(StrEnum):
    """Result of trying to reserve one semantic computation."""

    OWNER = "owner"
    WAITING = "waiting"
    CACHE_HIT = "cache_hit"


@dataclass(frozen=True, slots=True)
class CacheReservationResult:
    """Single-flight election result and optional reusable entry."""

    disposition: CacheReservationDisposition
    entry: ComputationCacheEntry | None = None


@dataclass(frozen=True, slots=True)
class _Reservation:
    owner: TaskId
    expires_at: datetime


class InMemoryComputationCache:
    """Deterministic process-local cache with atomic owner election."""

    def __init__(self, *, artifact_index: _ArtifactAvailability) -> None:
        self._artifact_index = artifact_index
        self._entries: dict[ComputationKey, ComputationCacheEntry] = {}
        self._reservations: dict[ComputationKey, _Reservation] = {}
        self._lock = RLock()

    def reserve(
        self,
        key: ComputationKey,
        task: TaskId,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> CacheReservationResult:
        """Return a valid hit, elect one owner, or tell a peer to wait."""
        if ttl <= timedelta(0):
            raise ValueError("reservation ttl must be positive")
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                if self._entry_is_valid(entry):
                    return CacheReservationResult(
                        CacheReservationDisposition.CACHE_HIT, entry
                    )
                del self._entries[key]

            reservation = self._reservations.get(key)
            if reservation is None or reservation.expires_at <= now:
                self._reservations[key] = _Reservation(task, now + ttl)
                return CacheReservationResult(CacheReservationDisposition.OWNER)
            if reservation.owner == task:
                return CacheReservationResult(CacheReservationDisposition.OWNER)
            return CacheReservationResult(CacheReservationDisposition.WAITING)

    def complete(
        self,
        key: ComputationKey,
        task: TaskId,
        entry: ComputationCacheEntry,
    ) -> None:
        """Publish a valid entry and release its single-flight waiters."""
        with self._lock:
            self._require_owner(key, task)
            if entry.key != key or not self._entry_is_valid(entry):
                raise InvalidCacheEntryError("cache entry outputs are not reusable")
            self._entries[key] = entry
            del self._reservations[key]

    def fail(self, key: ComputationKey, task: TaskId) -> None:
        """Release a failed owner's reservation so a waiter can retry."""
        with self._lock:
            self._require_owner(key, task)
            del self._reservations[key]

    def _require_owner(self, key: ComputationKey, task: TaskId) -> None:
        reservation = self._reservations.get(key)
        if reservation is None or reservation.owner != task:
            raise ReservationOwnerError("task does not own the computation reservation")

    def _entry_is_valid(self, entry: ComputationCacheEntry) -> bool:
        for output in entry.outputs:
            if output.artifact_identity is None:
                continue
            try:
                self._artifact_index.get_artifact(output.artifact_identity)
            except LookupError:
                return False
            if not self._artifact_index.get_active_locations(output.artifact_identity):
                return False
        return True
