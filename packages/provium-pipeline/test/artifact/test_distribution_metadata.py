from __future__ import annotations

import tomllib
from pathlib import Path


def test_builtin_artifact_adapters_are_registered_as_entry_points() -> None:
    project = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    entry_points = project["project"]["entry-points"]

    assert entry_points["provium.artifact_stores"] == {
        "filesystem": (
            "provium_pipeline.artifact.plugins:filesystem_artifact_store_factory"
        )
    }
    assert entry_points["provium.artifact_indexes"] == {
        "sqlite": "provium_pipeline.artifact.plugins:sqlite_artifact_index_factory"
    }
