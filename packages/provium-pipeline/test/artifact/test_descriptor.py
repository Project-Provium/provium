from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import provium_pipeline.artifact as artifact_module
from provium import FinalizedArtifactInspection
from provium_pipeline.artifact import (
    ArtifactStoreCorruptionError,
    describe_finalized_artifact,
)


def inspection(*, created_at: datetime | None) -> FinalizedArtifactInspection:
    return cast(
        FinalizedArtifactInspection,
        SimpleNamespace(
            artifact_identity="artifact-identity",
            artifact_identifier="example.document",
            body_digest="body-digest",
            container_digest="container-digest",
            size_bytes=42,
            created_at=created_at,
        ),
    )


def test_descriptor_uses_fully_verified_core_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("artifact.pa")
    created_at = datetime(2026, 8, 22, tzinfo=UTC)
    inspected_paths: list[Path] = []

    def inspect(candidate: Path) -> FinalizedArtifactInspection:
        inspected_paths.append(candidate)
        return inspection(created_at=created_at)

    monkeypatch.setattr(artifact_module, "inspect_finalized_artifact", inspect)

    descriptor = describe_finalized_artifact(path)

    assert inspected_paths == [path]
    assert descriptor.identity == "artifact-identity"
    assert descriptor.artifact_identifier == "example.document"
    assert descriptor.body_digest == "body-digest"
    assert descriptor.container_digest == "container-digest"
    assert descriptor.size_bytes == 42
    assert descriptor.created_at == created_at


@pytest.mark.parametrize(
    ("inspection_failure", "message"),
    [
        (ValueError("body digest mismatch"), "body digest mismatch"),
        (None, "creation timestamp"),
    ],
)
def test_descriptor_rejects_corrupt_or_legacy_incomplete_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    inspection_failure: ValueError | None,
    message: str,
) -> None:
    def inspect(_: Path) -> FinalizedArtifactInspection:
        if inspection_failure is not None:
            raise inspection_failure
        return inspection(created_at=None)

    monkeypatch.setattr(artifact_module, "inspect_finalized_artifact", inspect)

    with pytest.raises(ArtifactStoreCorruptionError, match=message):
        describe_finalized_artifact(Path("artifact.pa"))
