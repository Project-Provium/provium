from provium_pipeline.retention import (
    InMemoryRetentionIndex,
    RetentionClass,
    RetentionReference,
    RetentionReferenceSource,
)


def _reference(
    source: RetentionReferenceSource,
    *,
    owner: str = "owner-1",
    retention_class: RetentionClass = RetentionClass.RUN,
) -> RetentionReference:
    return RetentionReference(
        artifact_identity="artifact-1",
        source=source,
        owner_identity=owner,
        retention_class=retention_class,
    )


def test_retention_reference_sources_and_classes_cover_owned_artifacts() -> None:
    assert {source.value for source in RetentionReferenceSource} == {
        "run-input",
        "run-output",
        "task-output",
        "computation-cache",
        "input-set",
        "user-pin",
        "active-attempt",
    }
    assert {retention_class.value for retention_class in RetentionClass} == {
        "pinned",
        "run",
        "cache",
        "ephemeral",
    }


def test_retention_index_is_idempotent_and_lists_deterministically() -> None:
    index = InMemoryRetentionIndex()
    later = _reference(RetentionReferenceSource.RUN_OUTPUT, owner="z-owner")
    earlier = _reference(RetentionReferenceSource.RUN_INPUT, owner="a-owner")

    index.add(later)
    index.add(earlier)
    index.add(earlier)

    assert index.list_for_artifact("artifact-1") == (earlier, later)
    assert index.is_retained("artifact-1")


def test_removing_one_shared_reference_keeps_artifact_retained() -> None:
    index = InMemoryRetentionIndex()
    run_reference = _reference(RetentionReferenceSource.RUN_OUTPUT)
    cache_reference = _reference(
        RetentionReferenceSource.COMPUTATION_CACHE,
        retention_class=RetentionClass.CACHE,
    )
    index.add(run_reference)
    index.add(cache_reference)

    index.remove(run_reference)
    index.remove(run_reference)

    assert index.list_for_artifact("artifact-1") == (cache_reference,)
    assert index.is_retained("artifact-1")
    index.remove(cache_reference)
    assert not index.is_retained("artifact-1")
    assert index.list_for_artifact("missing") == ()
