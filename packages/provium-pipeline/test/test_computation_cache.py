from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock, Thread
from uuid import UUID

import pytest

from provium_pipeline.computation_cache import (
    CachedOutput,
    CacheReservationDisposition,
    ComputationCacheEntry,
    InMemoryComputationCache,
    InvalidCacheEntryError,
    ReservationOwnerError,
)
from provium_pipeline.identifiers import ComputationKey, TaskId


class _ArtifactIndex:
    def __init__(self, available: set[str]) -> None:
        self.available = available
        self.located = set(available)

    def get_artifact(self, artifact_identity: str) -> object:
        if artifact_identity not in self.available:
            raise KeyError(artifact_identity)
        return object()

    def get_active_locations(self, artifact_identity: str) -> tuple[object, ...]:
        return (object(),) if artifact_identity in self.located else ()


_NOW = datetime(2026, 8, 23, tzinfo=UTC)
_KEY = ComputationKey("a" * 64)
_OWNER = TaskId(UUID("00000000-0000-0000-0000-000000000001"))
_WAITER = TaskId(UUID("00000000-0000-0000-0000-000000000002"))


def test_cache_entry_preserves_order_and_explicit_optional_absence() -> None:
    entry = ComputationCacheEntry(
        key=_KEY,
        outputs=(
            CachedOutput("required", "artifact-1"),
            CachedOutput("optional", None),
        ),
        created_at=_NOW,
    )

    assert entry.outputs == (
        CachedOutput("required", "artifact-1"),
        CachedOutput("optional", None),
    )


def test_completion_is_reused_only_while_all_produced_outputs_are_active() -> None:
    index = _ArtifactIndex({"artifact-1"})
    cache = InMemoryComputationCache(artifact_index=index)
    owner = cache.reserve(_KEY, _OWNER, now=_NOW, ttl=timedelta(minutes=1))
    entry = ComputationCacheEntry(
        key=_KEY,
        outputs=(
            CachedOutput("required", "artifact-1"),
            CachedOutput("optional", None),
        ),
        created_at=_NOW,
    )

    assert owner.disposition is CacheReservationDisposition.OWNER
    cache.complete(_KEY, _OWNER, entry)
    hit = cache.reserve(_KEY, _WAITER, now=_NOW, ttl=timedelta(minutes=1))
    assert hit.disposition is CacheReservationDisposition.CACHE_HIT
    assert hit.entry == entry

    index.located.clear()
    replacement = cache.reserve(
        _KEY, _WAITER, now=_NOW + timedelta(seconds=1), ttl=timedelta(minutes=1)
    )
    assert replacement.disposition is CacheReservationDisposition.OWNER
    assert replacement.entry is None


def test_completion_rejects_missing_or_locationless_outputs() -> None:
    cache = InMemoryComputationCache(artifact_index=_ArtifactIndex(set()))
    cache.reserve(_KEY, _OWNER, now=_NOW, ttl=timedelta(minutes=1))
    entry = ComputationCacheEntry(
        key=_KEY,
        outputs=(CachedOutput("required", "missing"),),
        created_at=_NOW,
    )

    with pytest.raises(InvalidCacheEntryError):
        cache.complete(_KEY, _OWNER, entry)


def test_one_owner_is_elected_and_peers_wait() -> None:
    cache = InMemoryComputationCache(artifact_index=_ArtifactIndex(set()))
    barrier = Barrier(3)
    lock = Lock()
    dispositions: list[CacheReservationDisposition] = []

    def reserve(task: TaskId) -> None:
        barrier.wait()
        result = cache.reserve(_KEY, task, now=_NOW, ttl=timedelta(minutes=1))
        with lock:
            dispositions.append(result.disposition)

    threads = [Thread(target=reserve, args=(task,)) for task in (_OWNER, _WAITER)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(item.value for item in dispositions) == ["owner", "waiting"]


def test_owner_reservation_is_idempotent_and_ttl_must_be_positive() -> None:
    cache = InMemoryComputationCache(artifact_index=_ArtifactIndex(set()))

    assert (
        cache.reserve(_KEY, _OWNER, now=_NOW, ttl=timedelta(seconds=10)).disposition
        is CacheReservationDisposition.OWNER
    )
    assert (
        cache.reserve(_KEY, _OWNER, now=_NOW, ttl=timedelta(seconds=10)).disposition
        is CacheReservationDisposition.OWNER
    )
    with pytest.raises(ValueError, match="positive"):
        cache.reserve(_KEY, _WAITER, now=_NOW, ttl=timedelta(0))


def test_empty_successful_output_contract_can_be_reused() -> None:
    cache = InMemoryComputationCache(artifact_index=_ArtifactIndex(set()))
    cache.reserve(_KEY, _OWNER, now=_NOW, ttl=timedelta(minutes=1))
    entry = ComputationCacheEntry(key=_KEY, outputs=(), created_at=_NOW)

    cache.complete(_KEY, _OWNER, entry)

    assert (
        cache.reserve(_KEY, _WAITER, now=_NOW, ttl=timedelta(minutes=1)).entry
        == entry
    )


def test_failure_or_expiry_allows_a_waiter_to_become_owner() -> None:
    cache = InMemoryComputationCache(artifact_index=_ArtifactIndex(set()))
    cache.reserve(_KEY, _OWNER, now=_NOW, ttl=timedelta(seconds=10))
    assert (
        cache.reserve(_KEY, _WAITER, now=_NOW, ttl=timedelta(seconds=10)).disposition
        is CacheReservationDisposition.WAITING
    )

    cache.fail(_KEY, _OWNER)
    assert (
        cache.reserve(_KEY, _WAITER, now=_NOW, ttl=timedelta(seconds=10)).disposition
        is CacheReservationDisposition.OWNER
    )

    later_owner = TaskId(UUID("00000000-0000-0000-0000-000000000003"))
    assert (
        cache.reserve(
            _KEY,
            later_owner,
            now=_NOW + timedelta(seconds=11),
            ttl=timedelta(seconds=10),
        ).disposition
        is CacheReservationDisposition.OWNER
    )


def test_only_the_reservation_owner_can_complete_or_fail() -> None:
    cache = InMemoryComputationCache(artifact_index=_ArtifactIndex(set()))
    cache.reserve(_KEY, _OWNER, now=_NOW, ttl=timedelta(minutes=1))
    entry = ComputationCacheEntry(key=_KEY, outputs=(), created_at=_NOW)

    with pytest.raises(ReservationOwnerError):
        cache.complete(_KEY, _WAITER, entry)
    with pytest.raises(ReservationOwnerError):
        cache.fail(_KEY, _WAITER)

    other_key = ComputationKey("b" * 64)
    with pytest.raises(InvalidCacheEntryError):
        cache.complete(
            _KEY,
            _OWNER,
            ComputationCacheEntry(key=other_key, outputs=(), created_at=_NOW),
        )
