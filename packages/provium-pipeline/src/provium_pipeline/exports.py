"""Exports generated from durable frozen run snapshots."""

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol, cast

from provium.canonical import canonical_json
from provium_pipeline.execution_codec import (
    pipeline_run_document,
    pipeline_run_from_json,
    pipeline_task_document,
    pipeline_task_from_json,
)
from provium_pipeline.identifiers import RunId
from provium_pipeline.run_models import PipelineRun, PipelineTask


class RunLookup(Protocol):
    """Minimal durable run lookup required by export services."""

    def get_run(self, identifier: RunId) -> PipelineRun: ...

    def list_tasks(self, run_identifier: RunId) -> tuple[PipelineTask, ...]: ...


@dataclass(frozen=True, slots=True)
class RunBundle:
    """Strictly reconstructed portable run export."""

    run: PipelineRun
    tasks: tuple[PipelineTask, ...]
    dispatches: tuple[dict[str, Any], ...]


def parse_run_bundle_json(payload: str) -> RunBundle:
    """Strictly parse a versioned canonical run bundle."""
    raw: object = json.loads(payload)
    if not isinstance(raw, dict):
        raise TypeError("run bundle must be a JSON object")
    value = cast(dict[str, object], raw)
    expected = {"schema", "run", "tasks", "dispatches"}
    if set(value) != expected:
        raise ValueError("run bundle has unknown or missing fields")
    if value["schema"] != "provium.run-bundle/v1":
        raise ValueError("unsupported run bundle schema")
    run_value = value["run"]
    tasks_value = value["tasks"]
    dispatches_value = value["dispatches"]
    if not isinstance(run_value, dict):
        raise TypeError("run bundle run must be an object")
    if not isinstance(tasks_value, list):
        raise TypeError("run bundle tasks must be an array")
    if not isinstance(dispatches_value, list):
        raise TypeError("run bundle dispatches must be an object array")
    dispatch_items = cast(list[object], dispatches_value)
    if not all(isinstance(item, dict) for item in dispatch_items):
        raise TypeError("run bundle dispatches must be an object array")
    run = pipeline_run_from_json(canonical_json(cast(Any, run_value)))
    tasks = tuple(
        pipeline_task_from_json(canonical_json(cast(Any, item)))
        for item in cast(list[object], tasks_value)
    )
    return RunBundle(
        run,
        tasks,
        tuple(cast(dict[str, Any], item) for item in dispatch_items),
    )


class RunExportService:
    """Export reproducible values from stored run state only."""

    def __init__(self, runs: RunLookup) -> None:
        self._runs = runs

    def configuration_document(self, identifier: RunId) -> dict[str, Any]:
        """Return an isolated copy of the run's resolved configuration."""
        run = self._runs.get_run(identifier)
        return deepcopy(run.pipeline.resolved_configuration.document)

    def configuration_json(self, identifier: RunId) -> str:
        """Return canonical JSON for the run's resolved configuration."""
        return canonical_json(cast(Any, self.configuration_document(identifier)))

    def inputs_ndjson(self, identifier: RunId) -> str:
        """Return deterministic NDJSON for the run's frozen input records."""
        run = self._runs.get_run(identifier)
        lines = (
            canonical_json(
                cast(
                    Any,
                    {
                        "key": str(record.key),
                        "inputs": {
                            name: list(identities)
                            for name, identities in record.inputs.items()
                        },
                        "labels": dict(record.labels),
                    },
                )
            )
            for record in run.inputs.records
        )
        return "".join(f"{line}\n" for line in lines)

    def run_bundle_document(self, identifier: RunId) -> dict[str, Any]:
        """Return the complete versioned local run export."""
        run = self._runs.get_run(identifier)
        tasks = sorted(
            self._runs.list_tasks(identifier),
            key=lambda task: str(task.identifier),
        )
        return {
            "schema": "provium.run-bundle/v1",
            "run": pipeline_run_document(run),
            "tasks": [pipeline_task_document(task) for task in tasks],
            "dispatches": [],
        }

    def run_bundle_json(self, identifier: RunId) -> str:
        """Return canonical JSON for the complete local run export."""
        return canonical_json(cast(Any, self.run_bundle_document(identifier)))


__all__ = [
    "RunBundle",
    "RunExportService",
    "RunLookup",
    "parse_run_bundle_json",
]
