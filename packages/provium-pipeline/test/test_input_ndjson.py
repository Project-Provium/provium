from __future__ import annotations

import pytest

from provium_pipeline import InputRecordDecodeError, load_input_records_ndjson
from provium_pipeline.identifiers import InputRecordKey


def test_ndjson_loader_preserves_record_and_binding_order() -> None:
    records = load_input_records_ndjson(
        """{"key":"record-b","inputs":{"images":["b","a"]},"labels":{}}

{"key":"record-a","inputs":{"images":["a"]},"labels":{"split":"eval"}}
""",
        source="records.ndjson",
    )

    assert tuple(record.key for record in records) == (
        InputRecordKey("record-b"),
        InputRecordKey("record-a"),
    )
    assert records[0].inputs["images"] == ("b", "a")


def test_ndjson_loader_reports_source_line_for_invalid_json() -> None:
    with pytest.raises(
        InputRecordDecodeError,
        match=r"records.ndjson:2: invalid JSON",
    ):
        load_input_records_ndjson(
            '{"key":"valid","inputs":{},"labels":{}}\n{bad}',
            source="records.ndjson",
        )


def test_ndjson_loader_rejects_invalid_shape_and_duplicate_keys() -> None:
    with pytest.raises(
        InputRecordDecodeError,
        match=r"records.ndjson:1: invalid input record \(inputs",
    ):
        load_input_records_ndjson(
            '{"key":"record","inputs":{"image":"not-a-list"},"labels":{}}',
            source="records.ndjson",
        )

    with pytest.raises(
        InputRecordDecodeError,
        match=r"records.ndjson:1: invalid input record \(key\)",
    ):
        load_input_records_ndjson(
            '{"key":"bad key","inputs":{},"labels":{}}',
            source="records.ndjson",
        )

    duplicate = (
        '{"key":"same","inputs":{},"labels":{}}\n{"key":"same","inputs":{},"labels":{}}'
    )
    with pytest.raises(
        InputRecordDecodeError,
        match=r"records.ndjson:2: duplicate input record key: same",
    ):
        load_input_records_ndjson(duplicate, source="records.ndjson")
