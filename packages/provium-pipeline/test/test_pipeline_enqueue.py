from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from provium import JsonValue
from provium_pipeline.cli import LocalCLIBackend, PipelineCommand
from provium_pipeline.definition.models import PipelineDefinition
from provium_pipeline.run.models import PipelineRun
from provium_pipeline.run.query import RunLookup


def test_pipeline_enqueue_creates_a_planned_run_from_resolver_configuration(
    tmp_path: Path,
) -> None:
    configuration = tmp_path / "resolver.yaml"
    configuration.write_text("minimum_age: 30\n", encoding="utf-8")
    parser = argparse.ArgumentParser()
    PipelineCommand().configure(parser)
    arguments = parser.parse_args(
        [
            "enqueue",
            "test/catalog_fixtures/yaml_pipeline.yaml",
            "--records-from",
            "example.ready-records",
            "--config",
            str(configuration),
            "--all",
        ]
    )

    class Creator:
        called = False

        def create(
            self,
            definition: PipelineDefinition,
            *,
            resolver_identifier: str,
            configuration: Mapping[str, JsonValue],
            metadata: Mapping[str, JsonValue] | None = None,
        ) -> PipelineRun:
            self.called = True
            assert definition.pipeline.identifier == "yaml-pipeline"
            assert resolver_identifier == "example.ready-records"
            assert configuration == {"minimum_age": 30}
            assert metadata == {
                "selection": {
                    "all": True,
                    "all_remaining": False,
                    "include_missing_upstream": False,
                    "labels": [],
                    "nodes": [],
                    "only_failed": False,
                    "procedures": [],
                    "records": [],
                }
            }
            return cast(
                PipelineRun,
                SimpleNamespace(
                    identifier="00000000-0000-0000-0000-000000000001",
                    state=SimpleNamespace(value="planned"),
                ),
            )

    creator = Creator()
    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        resolved_run_creator=creator,
    )

    result = backend.execute("pipeline", "enqueue", arguments)

    assert creator.called
    assert result.exit_code == 0
    assert result.data == {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "status": "planned",
    }


def test_pipeline_enqueue_requires_resolver_options() -> None:
    parser = argparse.ArgumentParser()
    PipelineCommand().configure(parser)
    arguments = parser.parse_args(["enqueue", "pipeline.yaml", "--all"])
    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        resolved_run_creator=cast(Any, object()),
    )

    with pytest.raises(
        ValueError,
        match="pipeline execution requires --records-from and --config",
    ):
        backend.execute("pipeline", "enqueue", arguments)


def test_pipeline_enqueue_rejects_non_mapping_resolver_configuration(
    tmp_path: Path,
) -> None:
    configuration = tmp_path / "resolver.yaml"
    configuration.write_text("- record-1\n", encoding="utf-8")
    parser = argparse.ArgumentParser()
    PipelineCommand().configure(parser)
    arguments = parser.parse_args(
        [
            "enqueue",
            "pipeline.yaml",
            "--records-from",
            "example.ready-records",
            "--config",
            str(configuration),
        ]
    )
    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        resolved_run_creator=cast(Any, object()),
    )

    with pytest.raises(
        TypeError,
        match="pipeline resolver configuration must be a mapping",
    ):
        backend.execute("pipeline", "enqueue", arguments)
