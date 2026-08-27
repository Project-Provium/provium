from __future__ import annotations

import argparse

import pytest

from provium_pipeline.cli import PipelineCommand


@pytest.mark.parametrize("action", ["enqueue", "execute"])
def test_pipeline_convenience_commands_parse_resolver_and_configuration(
    action: str,
) -> None:
    parser = argparse.ArgumentParser()
    PipelineCommand().configure(parser)

    arguments = parser.parse_args(
        [
            action,
            "pipeline.yaml",
            "--records-from",
            "example.ready-records",
            "--config",
            "production.yaml",
        ]
    )

    assert arguments.source == "pipeline.yaml"
    assert arguments.records_from == "example.ready-records"
    assert arguments.config == "production.yaml"
