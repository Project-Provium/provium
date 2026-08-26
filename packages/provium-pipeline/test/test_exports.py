import json
from datetime import UTC, datetime

import pytest

from provium_pipeline.execution_store import InMemoryExecutionStore
from provium_pipeline.exports import RunExportService, parse_run_bundle_json
from provium_pipeline.input_codec import load_input_records_ndjson
from test.test_execution_store import request


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("[]", "must be a JSON object"),
        ('{"schema":"provium.run-bundle/v1"}', "unknown or missing fields"),
        (
            '{"schema":"wrong","run":{},"tasks":[],"dispatches":[]}',
            "unsupported run bundle schema",
        ),
        (
            '{"schema":"provium.run-bundle/v1","run":[],"tasks":[],"dispatches":[]}',
            "run must be an object",
        ),
        (
            '{"schema":"provium.run-bundle/v1","run":{},"tasks":{},"dispatches":[]}',
            "tasks must be an array",
        ),
        (
            '{"schema":"provium.run-bundle/v1","run":{},"tasks":[],"dispatches":{}}',
            "dispatches must be an object array",
        ),
        (
            '{"schema":"provium.run-bundle/v1","run":{},"tasks":[],"dispatches":[1]}',
            "dispatches must be an object array",
        ),
    ],
)
def test_run_bundle_parser_rejects_invalid_envelopes(
    payload: str,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        parse_run_bundle_json(payload)


def test_configuration_export_uses_frozen_run_snapshot_and_is_canonical() -> None:
    store = InMemoryExecutionStore(clock=lambda: datetime(2026, 8, 26, tzinfo=UTC))
    run = store.create_run(request(key=None))
    exports = RunExportService(store)

    document = exports.configuration_document(run.identifier)
    payload = exports.configuration_json(run.identifier)

    assert document == run.pipeline.resolved_configuration.document
    assert json.loads(payload) == document
    assert payload == exports.configuration_json(run.identifier)
    document["nodes"] = {"tampered": True}
    assert exports.configuration_document(run.identifier) == (
        run.pipeline.resolved_configuration.document
    )


def test_inputs_export_round_trips_frozen_records_as_deterministic_ndjson() -> None:
    store = InMemoryExecutionStore(clock=lambda: datetime(2026, 8, 26, tzinfo=UTC))
    run = store.create_run(request(key=None))
    exports = RunExportService(store)

    payload = exports.inputs_ndjson(run.identifier)

    assert load_input_records_ndjson(payload) == run.inputs.records
    assert payload.endswith("\n")
    assert payload == exports.inputs_ndjson(run.identifier)


def test_run_bundle_is_versioned_canonical_and_round_trips() -> None:
    store = InMemoryExecutionStore(clock=lambda: datetime(2026, 8, 26, tzinfo=UTC))
    run = store.create_run(request(key=None))
    tasks = store.list_tasks(run.identifier)
    exports = RunExportService(store)

    payload = exports.run_bundle_json(run.identifier)
    restored = parse_run_bundle_json(payload)

    assert json.loads(payload)["schema"] == "provium.run-bundle/v1"
    assert restored.run == run
    assert restored.tasks == tasks
    assert restored.dispatches == ()
    assert payload == exports.run_bundle_json(run.identifier)
