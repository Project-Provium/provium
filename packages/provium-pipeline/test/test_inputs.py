from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from provium_pipeline import (
    InputRecord,
    InputSet,
    InputSourceDescriptor,
    InputSourceKind,
    RunInputSnapshot,
)
from provium_pipeline.identifiers import InputRecordKey, InputSetIdentifier


def test_input_record_copies_bindings_into_ordered_immutable_tuples() -> None:
    identities = ["artifact-b", "artifact-a"]
    bindings = {"source": identities}
    labels = {"partition": "north"}

    record = InputRecord(
        key=InputRecordKey("record-1"),
        inputs=bindings,
        labels=labels,
    )
    identities.append("artifact-c")
    bindings["other"] = ["artifact-d"]
    labels["partition"] = "south"

    assert record.inputs == {"source": ("artifact-b", "artifact-a")}
    assert record.labels == {"partition": "north"}
    with pytest.raises(TypeError):
        record.inputs["other"] = ("artifact-d",)  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        record.key = InputRecordKey("other")  # type: ignore[misc]


def test_input_set_is_an_immutable_named_record_snapshot() -> None:
    record = InputRecord(
        key=InputRecordKey("record-1"),
        inputs={"source": ("artifact-a",)},
        labels={},
    )
    records = [record]
    metadata = {"owner": "evaluation"}
    created_at = datetime(2026, 8, 23, tzinfo=UTC)

    input_set = InputSet(
        identity="input-set:sha256:digest",
        identifier=InputSetIdentifier("evaluation-v1"),
        records=records,
        digest="digest",
        created_at=created_at,
        metadata=metadata,
    )
    records.clear()
    metadata["owner"] = "changed"

    assert input_set.records == (record,)
    assert input_set.metadata == {"owner": "evaluation"}
    with pytest.raises(TypeError):
        input_set.metadata["owner"] = "changed"  # type: ignore[index]


def test_run_input_snapshot_is_canonical_and_preserves_repeated_order() -> None:
    second = InputRecord(
        key=InputRecordKey("record-b"),
        inputs={"images": ("artifact-2", "artifact-1")},
        labels={},
    )
    first = InputRecord(
        key=InputRecordKey("record-a"),
        inputs={"images": ("artifact-1",)},
        labels={"split": "evaluation"},
    )
    source = InputSourceDescriptor(
        kind=InputSourceKind.DIRECT,
        identifier="cli",
        configuration={},
        result_digest="resolved-digest",
    )

    snapshot = RunInputSnapshot.create(
        records=(second, first),
        shared_inputs={"model": ("model-b", "model-a")},
        sources=(source,),
    )
    equivalent = RunInputSnapshot.create(
        records=(first, second),
        shared_inputs={"model": ("model-b", "model-a")},
        sources=(source,),
    )
    reordered = RunInputSnapshot.create(
        records=(first, second),
        shared_inputs={"model": ("model-a", "model-b")},
        sources=(source,),
    )

    assert tuple(record.key for record in snapshot.records) == (
        InputRecordKey("record-a"),
        InputRecordKey("record-b"),
    )
    assert snapshot.digest == equivalent.digest
    assert snapshot.digest != reordered.digest


def test_run_input_snapshot_rejects_duplicate_record_keys() -> None:
    record = InputRecord(key=InputRecordKey("duplicate"), inputs={}, labels={})

    with pytest.raises(ValueError, match="duplicate input record key: duplicate"):
        RunInputSnapshot.create(records=(record, record), shared_inputs={}, sources=())
