from datetime import UTC, datetime
from pathlib import Path

import pytest

from provium_pipeline.identifiers import InputRecordKey, InputSetIdentifier
from provium_pipeline.inputs import InputRecord
from provium_pipeline.sqlite_input_sets import SQLiteInputSetStore


def record(key: str, artifact: str) -> InputRecord:
    return InputRecord(
        key=InputRecordKey(key),
        inputs={"document": (artifact,)},
        labels={"split": "evaluation"},
    )


def test_sqlite_input_set_store_creates_reopens_and_lists_immutable_sets(
    tmp_path: Path,
) -> None:
    database = tmp_path / "input-sets.sqlite3"
    now = datetime(2026, 8, 26, tzinfo=UTC)
    store = SQLiteInputSetStore(database, clock=lambda: now)

    first = store.create(
        identifier=InputSetIdentifier("evaluation-v1"),
        records=(record("record-b", "artifact-2"), record("record-a", "artifact-1")),
        metadata={"owner": "quality"},
    )
    replay = store.create(
        identifier=InputSetIdentifier("evaluation-v1"),
        records=(record("record-a", "artifact-1"), record("record-b", "artifact-2")),
        metadata={"owner": "quality"},
    )

    assert replay == first
    assert first.identity == first.digest
    assert first.created_at == now
    assert tuple(item.key for item in first.records) == (
        InputRecordKey("record-a"),
        InputRecordKey("record-b"),
    )
    reopened = SQLiteInputSetStore(database)
    assert reopened.get(first.identity) == first
    assert reopened.get(InputSetIdentifier("evaluation-v1")) == first
    assert reopened.list() == (first,)


def test_sqlite_input_set_store_rejects_identifier_redefinition_and_unknown_sets(
    tmp_path: Path,
) -> None:
    store = SQLiteInputSetStore(tmp_path / "input-sets.sqlite3")
    store.create(
        identifier=InputSetIdentifier("evaluation-v1"),
        records=(record("record-a", "artifact-1"),),
        metadata={},
    )

    with pytest.raises(ValueError, match="record keys must be unique"):
        store.create(
            identifier=InputSetIdentifier("duplicate-records"),
            records=(
                record("record-a", "artifact-1"),
                record("record-a", "artifact-1"),
            ),
            metadata={},
        )
    with pytest.raises(ValueError, match="immutable input set identifier"):
        store.create(
            identifier=InputSetIdentifier("evaluation-v1"),
            records=(record("record-a", "artifact-2"),),
            metadata={},
        )
    with pytest.raises(KeyError, match="unknown input set"):
        store.get("missing")
