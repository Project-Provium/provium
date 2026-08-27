from __future__ import annotations

import argparse
from typing import cast

from provium_pipeline.cli import LocalCLIBackend
from provium_pipeline.identifiers import RunId
from provium_pipeline.run_query import RunLookup


def test_run_artifacts_returns_provider_results_with_locations() -> None:
    run_identifier = RunId.new()
    calls: list[tuple[str, bool]] = []

    def artifacts(identifier: str, include_locations: bool) -> tuple[object, ...]:
        calls.append((identifier, include_locations))
        return (
            {
                "binding": {"artifact_identity": "artifact-1"},
                "locations": [{"store_identifier": "local"}],
            },
        )

    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        run_artifacts=artifacts,
    )
    result = backend.execute(
        "run",
        "artifacts",
        argparse.Namespace(run_id=str(run_identifier), locations=True),
    )

    assert calls == [(str(run_identifier), True)]
    assert result.data == {
        "artifacts": [
            {
                "binding": {"artifact_identity": "artifact-1"},
                "locations": [{"store_identifier": "local"}],
            }
        ]
    }
