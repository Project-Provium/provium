from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from provium_pipeline import InputRecord
from provium_pipeline.identifiers import InputRecordKey


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
