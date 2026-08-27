from types import SimpleNamespace
from typing import cast
from uuid import UUID

from pytest import MonkeyPatch

from provium_pipeline import exports
from provium_pipeline.identifiers import RunId
from provium_pipeline.run.exports import RunExportService, RunLookup


class Store:
    def get_run(self, identifier: RunId) -> object:
        return SimpleNamespace(identifier=identifier)

    def list_tasks(self, identifier: RunId) -> tuple[object, ...]:
        return (
            SimpleNamespace(identifier="task-b"),
            SimpleNamespace(identifier="task-a"),
        )


def test_run_bundle_includes_sorted_secret_free_dispatch_summaries(
    monkeypatch: MonkeyPatch,
) -> None:
    def run_document(run: object) -> dict[str, str]:
        value = cast(SimpleNamespace, run)
        return {"id": str(value.identifier)}

    def task_document(task: object) -> dict[str, str]:
        value = cast(SimpleNamespace, task)
        return {"id": str(value.identifier)}

    monkeypatch.setattr(exports, "pipeline_run_document", run_document)
    monkeypatch.setattr(exports, "pipeline_task_document", task_document)
    service = RunExportService(
        cast(RunLookup, Store()),
        dispatch_summaries=lambda run_id: (
            {"id": "dispatch-b", "status": "done", "secret_token": "hidden"},
            {
                "id": "dispatch-a",
                "details": {
                    "password": "hidden",
                    "worker": 2,
                    "workers": [{"token": "hidden", "id": 2}],
                },
            },
        ),
    )

    document = service.run_bundle_document(RunId(UUID(int=1)))

    assert document["dispatches"] == [
        {
            "details": {"worker": 2, "workers": [{"id": 2}]},
            "id": "dispatch-a",
        },
        {"id": "dispatch-b", "status": "done"},
    ]
