import json
from datetime import UTC, datetime

from provium_pipeline.execution_store import InMemoryExecutionStore
from provium_pipeline.exports import RunExportService
from test.test_execution_store import request


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
