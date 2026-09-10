"""Public inspection and verification of finalized artifact containers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import BinaryIO

from provium.artifact.header import read_artifact_header
from provium.provenance import ArtifactLineage

_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class FinalizedArtifactInspection:
    """Verified immutable metadata for one finalized artifact container."""

    path: Path
    artifact_identifier: str
    artifact_identity: str
    body_digest: str
    container_digest: str
    size_bytes: int
    created_at: datetime | None
    lineage: ArtifactLineage


def _digest_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(_CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def inspect_finalized_artifact(
    path: str | PathLike[str],
) -> FinalizedArtifactInspection:
    """Fully verify a finalized artifact without resolving its artifact class."""
    finalized_path = Path(path).resolve()
    header = read_artifact_header(finalized_path)
    size_bytes = finalized_path.stat().st_size
    expected_size = header.body_offset + header.body_length
    if size_bytes != expected_size:
        raise ValueError(
            "artifact container size does not match its finalized header: "
            f"expected {expected_size}, found {size_bytes}"
        )

    with finalized_path.open("rb") as stream:
        container_digest = _digest_stream(stream)
        stream.seek(header.body_offset)
        body_digest = _digest_stream(stream)
    if body_digest != header.body_digest:
        raise ValueError("artifact body digest does not match its finalized header")

    return FinalizedArtifactInspection(
        path=finalized_path,
        artifact_identifier=header.artifact_identifier,
        artifact_identity=header.artifact_identity,
        body_digest=header.body_digest,
        container_digest=container_digest,
        size_bytes=size_bytes,
        created_at=header.created_at,
        lineage=header.lineage,
    )


__all__ = ["FinalizedArtifactInspection", "inspect_finalized_artifact"]
