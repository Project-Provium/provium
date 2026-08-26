"""Exports generated from durable frozen run snapshots."""

from copy import deepcopy
from typing import Any, Protocol, cast

from provium.canonical import canonical_json
from provium_pipeline.identifiers import RunId
from provium_pipeline.run_models import PipelineRun


class RunLookup(Protocol):
    """Minimal durable run lookup required by export services."""

    def get_run(self, identifier: RunId) -> PipelineRun: ...


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


__all__ = ["RunExportService", "RunLookup"]
