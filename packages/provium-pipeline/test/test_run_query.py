from collections.abc import Mapping
from types import SimpleNamespace
from typing import cast

from provium_pipeline.run.query import RunLookup, RunQueryService


class Store:
    def get_run(self, identifier: str) -> object:
        assert identifier == "run-1"
        return SimpleNamespace(
            identifier="run-1",
            state=SimpleNamespace(value="running"),
            inputs={"record-b": {"x": 2}, "record-a": {"x": 1}},
            expected_outputs=("expected",),
        )

    def list_tasks(self, identifier: str) -> tuple[object, ...]:
        assert identifier == "run-1"
        return (
            SimpleNamespace(
                identifier="task-b",
                record_key="record-b",
                node_identifier="detect",
                state=SimpleNamespace(value="succeeded"),
                expected_output_fields=("detections",),
            ),
            SimpleNamespace(
                identifier="task-a",
                record_key="record-a",
                node_identifier="load",
                state=SimpleNamespace(value="ready"),
                expected_output_fields=("document",),
            ),
        )


def test_run_query_builds_complete_deterministic_read_model() -> None:
    service = RunQueryService(
        cast(RunLookup, Store()),
        attempts=lambda run_id: ({"task_id": "task-b", "attempt": 1},),
        cache_dispositions=lambda run_id: {"task-b": "miss", "task-a": "hit"},
        outputs=lambda run_id: ({"field": "detections", "artifact": "sha256:x"},),
        audit_events=lambda run_id: (
            {"sequence": 2, "event": "task_succeeded"},
            {"sequence": 1, "event": "run_created"},
        ),
    )

    view = service.get("run-1")

    assert view.status == "running"
    assert view.status_counts == {"ready": 1, "succeeded": 1}
    assert [row.task_id for row in view.tasks] == ["task-a", "task-b"]
    assert [row.cache_disposition for row in view.tasks] == ["hit", "miss"]
    assert view.inputs == {"record-a": {"x": 1}, "record-b": {"x": 2}}
    assert view.expected_outputs == ("expected",)
    assert view.outputs == ({"field": "detections", "artifact": "sha256:x"},)
    assert view.attempts == ({"task_id": "task-b", "attempt": 1},)
    timeline = cast(tuple[Mapping[str, object], ...], view.audit_timeline)
    assert [event["sequence"] for event in timeline] == [1, 2]


def test_run_query_defaults_optional_observability_sources_to_empty() -> None:
    view = RunQueryService(cast(RunLookup, Store())).get("run-1")

    assert view.attempts == ()
    assert view.outputs == ()
    assert view.audit_timeline == ()
    assert [row.cache_disposition for row in view.tasks] == [None, None]


def test_run_query_orders_opaque_or_malformed_audit_events_without_failing() -> None:
    opaque = object()
    malformed = {"sequence": "unknown"}
    view = RunQueryService(
        cast(RunLookup, Store()),
        audit_events=lambda run_id: (opaque, malformed),
    ).get("run-1")

    assert view.audit_timeline == (opaque, malformed)


def test_run_query_preserves_typed_input_snapshots() -> None:
    snapshot = object()

    class TypedInputStore(Store):
        def get_run(self, identifier: str) -> object:
            run = cast(SimpleNamespace, super().get_run(identifier))
            run.inputs = snapshot
            return run

    service = RunQueryService(cast(RunLookup, TypedInputStore()))
    assert service.get("run-1").inputs is snapshot
