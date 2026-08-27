from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from provium import JsonValue
from provium_pipeline.cli import LocalCLIBackend, PipelineCommand
from provium_pipeline.definition.models import PipelineDefinition
from provium_pipeline.dispatch.models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    RetryPolicy,
    TaskSelection,
)
from provium_pipeline.identifiers import DispatchId, RunId
from provium_pipeline.run.models import PipelineRun
from provium_pipeline.run.query import RunLookup


def test_pipeline_execute_creates_and_executes_resolver_backed_run(
    tmp_path: Path,
) -> None:
    configuration_path = tmp_path / "resolver.json"
    configuration_path.write_text('{"minimum_age":30}', encoding="utf-8")
    parser = argparse.ArgumentParser()
    PipelineCommand().configure(parser)
    arguments = parser.parse_args(
        [
            "execute",
            "test/catalog_fixtures/yaml_pipeline.yaml",
            "--records-from",
            "example.ready-records",
            "--config",
            str(configuration_path),
            "--node",
            "normalize",
            "--include-missing-upstream",
        ]
    )
    run_identifier = RunId.parse("00000000-0000-0000-0000-000000000001")

    class Creator:
        def create(
            self,
            definition: PipelineDefinition,
            *,
            resolver_identifier: str,
            configuration: Mapping[str, JsonValue],
            metadata: Mapping[str, JsonValue] | None = None,
        ) -> PipelineRun:
            assert definition.pipeline.identifier == "yaml-pipeline"
            assert resolver_identifier == "example.ready-records"
            assert configuration == {"minimum_age": 30}
            return cast(
                PipelineRun,
                SimpleNamespace(identifier=run_identifier),
            )

    class Executor:
        call: tuple[RunId, TaskSelection, DependencyPolicy] | None = None

        def execute(
            self,
            run_identifier: RunId,
            *,
            selection: TaskSelection,
            dependency_policy: DependencyPolicy,
        ) -> Dispatch:
            self.call = (run_identifier, selection, dependency_policy)
            return Dispatch(
                identifier=DispatchId.new(),
                run_identifier=run_identifier,
                selection=selection,
                task_identifiers=(),
                dependency_policy=dependency_policy,
                retry_policy=RetryPolicy(max_attempts=1),
                state=DispatchState.SUCCEEDED,
                created_at=datetime(2026, 8, 27, tzinfo=UTC),
                terminal_at=datetime(2026, 8, 27, tzinfo=UTC),
            )

    executor = Executor()
    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        resolved_run_creator=Creator(),
        run_executor=executor,
    )

    result = backend.execute("pipeline", "execute", arguments)

    assert executor.call is not None
    identifier, selection, dependency_policy = executor.call
    assert identifier == run_identifier
    assert selection.nodes == ("normalize",)
    assert dependency_policy is DependencyPolicy.INCLUDE_MISSING_UPSTREAM
    assert "dispatch" in result.data
