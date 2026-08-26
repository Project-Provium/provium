from datetime import UTC, datetime, timedelta

import pytest

from provium_pipeline.gc import GarbageCollectionObject, GarbageCollectionService
from provium_pipeline.retention import (
    InMemoryRetentionIndex,
    RetentionClass,
    RetentionReference,
    RetentionReferenceSource,
)

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def test_gc_plan_freezes_only_old_unretained_locations_deterministically() -> None:
    retention = InMemoryRetentionIndex()
    retention.add(
        RetentionReference(
            artifact_identity="retained",
            source=RetentionReferenceSource.USER_PIN,
            owner_identity="pin-1",
            retention_class=RetentionClass.PINNED,
        )
    )
    service = GarbageCollectionService(
        retention=retention,
        delete=lambda _location: None,
        plan_id_factory=lambda: "plan-1",
    )
    old_b = GarbageCollectionObject("orphan-b", "location-b", NOW - timedelta(days=2))
    old_a = GarbageCollectionObject("orphan-a", "location-a", NOW - timedelta(days=2))
    retained = GarbageCollectionObject(
        "retained", "location-r", NOW - timedelta(days=2)
    )
    young = GarbageCollectionObject("young", "location-y", NOW)

    plan = service.plan(
        (old_b, retained, young, old_a),
        now=NOW,
        grace_period=timedelta(days=1),
    )

    assert plan.identifier == "plan-1"
    assert plan.candidates == (old_a, old_b)
    with pytest.raises(ValueError, match="grace period cannot be negative"):
        service.plan((), now=NOW, grace_period=timedelta(seconds=-1))


def test_gc_apply_revalidates_races_and_is_idempotent() -> None:
    retention = InMemoryRetentionIndex()
    deleted: list[str] = []
    service = GarbageCollectionService(
        retention=retention,
        delete=deleted.append,
        plan_id_factory=lambda: "plan-1",
    )
    candidate = GarbageCollectionObject(
        "artifact-1", "location-1", NOW - timedelta(days=2)
    )
    raced = GarbageCollectionObject("artifact-2", "location-2", NOW - timedelta(days=2))
    plan = service.plan((candidate, raced), now=NOW, grace_period=timedelta(days=1))
    retention.add(
        RetentionReference(
            artifact_identity="artifact-2",
            source=RetentionReferenceSource.RUN_OUTPUT,
            owner_identity="run-1",
            retention_class=RetentionClass.RUN,
        )
    )

    first = service.apply(plan)
    second = service.apply(plan)

    assert first is second
    assert first.deleted == (candidate,)
    assert first.skipped == (raced,)
    assert deleted == ["location-1"]

    conflicting = service.plan(
        (GarbageCollectionObject("artifact-3", "location-3", NOW - timedelta(days=2)),),
        now=NOW,
        grace_period=timedelta(days=1),
    )
    with pytest.raises(RuntimeError, match="plan identifier collision"):
        service.apply(conflicting)
