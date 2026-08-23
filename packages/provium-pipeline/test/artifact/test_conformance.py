from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from provium_pipeline.artifact import ArtifactStore, run_artifact_store_conformance


class BrokenStore:
    def __init__(self, failure: str) -> None:
        self.failure = failure
        self.publications = 0

    def publish(self, **kwargs: Any) -> SimpleNamespace:
        del kwargs
        self.publications += 1
        locator = self.publications if self.failure == "publication" else 1
        return SimpleNamespace(locator=locator)

    def stat(self, location: object) -> None:
        del location

    def materialize(self, *, destination: Path, **kwargs: Any) -> None:
        del kwargs
        if self.failure != "materialization":
            destination.write_bytes(b"artifact")

    def delete(self, location: object) -> None:
        del location


def run_broken_store(tmp_path: Path, failure: str) -> None:
    run_artifact_store_conformance(
        store=cast(ArtifactStore, BrokenStore(failure)),
        source=tmp_path / "source.pa",
        descriptor=cast(Any, object()),
        workspace=tmp_path / "workspace",
    )


def test_artifact_store_conformance_runner_is_public() -> None:
    assert callable(run_artifact_store_conformance)


def test_conformance_rejects_non_idempotent_publication(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="different locator"):
        run_broken_store(tmp_path, "publication")


def test_conformance_rejects_missing_materialization(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="did not create"):
        run_broken_store(tmp_path, "materialization")


def test_conformance_rejects_readable_deleted_artifact(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="remains readable"):
        run_broken_store(tmp_path, "deletion")
