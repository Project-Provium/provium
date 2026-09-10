"""Tests for standalone finalized artifact inspection."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from provium import (
    ArtifactHeader,
    ArtifactLineage,
    ArtifactRecord,
    ArtifactReference,
    ProcedureExecutionRecord,
    ProcedureRecord,
    encode_header,
    inspect_finalized_artifact,
)


def artifact_header(
    body: bytes,
    *,
    created_at: datetime | None = datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
    legacy: bool = False,
) -> ArtifactHeader:
    reference = ArtifactReference("artifact-1", "example.ImageV1")
    execution = ProcedureExecutionRecord(
        "execution-1",
        ProcedureRecord("example.CreateV1", "contract-digest"),
        outputs=(reference,),
    )
    body_digest = hashlib.sha256(body).hexdigest()
    lineage = ArtifactLineage.for_execution(
        execution,
        (ArtifactRecord(reference, body_digest, execution.identity),),
    )
    if legacy:
        return ArtifactHeader(
            artifact_identifier=reference.artifact_identifier,
            artifact_identity=reference.identity,
            body_offset=4096,
            body_length=len(body),
            body_digest=body_digest,
            lineage=lineage,
            created_at=None,
        )
    return ArtifactHeader.create(
        artifact_identifier=reference.artifact_identifier,
        artifact_identity=reference.identity,
        body_length=len(body),
        body_digest=body_digest,
        lineage=lineage,
        created_at=created_at,
    )


def write_container(path: Path, body: bytes, header: ArtifactHeader) -> None:
    encoded = encode_header(header)
    path.write_bytes(encoded + bytes(header.body_offset - len(encoded)) + body)


def test_inspection_verifies_and_describes_a_finalized_container(
    tmp_path: Path,
) -> None:
    body = b"verified artifact body"
    header = artifact_header(body)
    path = tmp_path / "artifact.provium"
    write_container(path, body, header)

    inspected = inspect_finalized_artifact(path)

    assert inspected.path == path.resolve()
    assert inspected.artifact_identifier == header.artifact_identifier
    assert inspected.artifact_identity == header.artifact_identity
    assert inspected.body_digest == header.body_digest
    assert inspected.container_digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert inspected.size_bytes == path.stat().st_size
    assert inspected.created_at == datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    assert inspected.lineage == header.lineage


def test_inspection_preserves_immutable_metadata_after_copy(tmp_path: Path) -> None:
    body = b"copy me"
    source = tmp_path / "source.provium"
    destination = tmp_path / "destination.provium"
    write_container(source, body, artifact_header(body))
    shutil.copyfile(source, destination)

    source_inspection = inspect_finalized_artifact(source)
    destination_inspection = inspect_finalized_artifact(destination)

    assert destination_inspection.created_at == source_inspection.created_at
    assert destination_inspection.container_digest == source_inspection.container_digest
    assert (
        destination_inspection.artifact_identity == source_inspection.artifact_identity
    )


def test_inspection_rejects_corrupt_and_truncated_bodies(tmp_path: Path) -> None:
    body = b"original"
    path = tmp_path / "artifact.provium"
    header = artifact_header(body)
    write_container(path, body, header)

    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValueError, match="body digest"):
        inspect_finalized_artifact(path)

    path.write_bytes(data[:-1])
    with pytest.raises(ValueError, match="container size"):
        inspect_finalized_artifact(path)


def test_inspection_accepts_legacy_headers_without_creation_time(
    tmp_path: Path,
) -> None:
    body = b"legacy"
    path = tmp_path / "legacy.provium"
    write_container(path, body, artifact_header(body, legacy=True))

    inspected = inspect_finalized_artifact(path)

    assert inspected.created_at is None
