from __future__ import annotations

from pathlib import Path

import pytest

from provium_pipeline.identifiers import InputRecordKey, RunId, TaskId
from provium_pipeline.task_outputs import (
    InMemoryTaskOutputStore,
    SQLiteTaskOutputStore,
    TaskOutputConflictError,
    TaskOutputSet,
)


def _outputs(**values: str | None) -> TaskOutputSet:
    return TaskOutputSet(
        task_identifier=TaskId.parse("00000000-0000-0000-0000-000000000002"),
        run_identifier=RunId.parse("00000000-0000-0000-0000-000000000001"),
        record_key=InputRecordKey("record-1"),
        node_identifier="decode",
        outputs=values,
    )


def test_in_memory_task_outputs_are_idempotent_and_resolvable() -> None:
    store = InMemoryTaskOutputStore()
    outputs = _outputs(image="artifact-1", preview=None)

    assert store.record(outputs) == outputs
    assert store.record(outputs) == outputs
    assert store.resolve(
        outputs.run_identifier,
        outputs.record_key,
        "$nodes.decode.image",
    ) == ("artifact-1",)
    assert (
        store.resolve(
            outputs.run_identifier,
            outputs.record_key,
            "$nodes.decode.preview",
        )
        == ()
    )
    assert store.list_for_run(outputs.run_identifier) == (outputs,)


def test_in_memory_task_outputs_reject_conflicting_completion() -> None:
    store = InMemoryTaskOutputStore()
    store.record(_outputs(image="artifact-1"))

    with pytest.raises(TaskOutputConflictError, match="already recorded"):
        store.record(_outputs(image="artifact-2"))


def test_sqlite_task_outputs_persist_and_match_in_memory_contract(
    tmp_path: Path,
) -> None:
    database = tmp_path / "pipeline.sqlite3"
    outputs = _outputs(preview=None, image="artifact-1")
    first = SQLiteTaskOutputStore(database)

    assert first.record(outputs) == outputs
    assert first.record(outputs) == outputs

    reopened = SQLiteTaskOutputStore(database)
    assert reopened.list_for_run(outputs.run_identifier) == (outputs,)
    assert reopened.resolve(
        outputs.run_identifier,
        outputs.record_key,
        "$nodes.decode.image",
    ) == ("artifact-1",)
    assert (
        reopened.resolve(
            outputs.run_identifier,
            outputs.record_key,
            "$nodes.decode.preview",
        )
        == ()
    )
    assert (
        reopened.resolve(
            outputs.run_identifier,
            InputRecordKey("other"),
            "$nodes.decode.image",
        )
        == ()
    )
    assert (
        reopened.resolve(
            outputs.run_identifier,
            outputs.record_key,
            "$inputs.image",
        )
        == ()
    )
    with pytest.raises(TaskOutputConflictError, match="already recorded"):
        reopened.record(_outputs(image="artifact-2"))


def test_in_memory_task_outputs_ignore_unrelated_or_invalid_references() -> None:
    store = InMemoryTaskOutputStore()
    outputs = _outputs(image="artifact-1")
    store.record(outputs)

    assert (
        store.resolve(
            outputs.run_identifier,
            InputRecordKey("other"),
            "$nodes.decode.image",
        )
        == ()
    )
    assert (
        store.resolve(
            outputs.run_identifier,
            outputs.record_key,
            "$inputs.image",
        )
        == ()
    )
    assert (
        store.resolve(
            outputs.run_identifier,
            outputs.record_key,
            "$nodes.decode",
        )
        == ()
    )
