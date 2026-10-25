"""Artifact definition interfaces."""

from provium.artifact.binding import ArtifactReadBinding, ArtifactWriteBinding
from provium.artifact.catalog import ArtifactCatalog
from provium.artifact.definition import Artifact, ArtifactDefinition
from provium.artifact.discovery import (
    discover_artifact_catalogs,
    reset_artifact_discovery,
)
from provium.artifact.header import (
    ArtifactHeader,
    decode_header,
    encode_header,
    read_artifact_header,
)
from provium.artifact.reader import ArtifactReader
from provium.artifact.region import BodyRegion
from provium.artifact.staging import StagedArtifact, stage_artifact
from provium.artifact.writer import ArtifactWriter

__all__ = [
    "Artifact",
    "ArtifactCatalog",
    "ArtifactDefinition",
    "ArtifactHeader",
    "ArtifactReadBinding",
    "ArtifactReader",
    "BodyRegion",
    "StagedArtifact",
    "decode_header",
    "ArtifactWriter",
    "ArtifactWriteBinding",
    "discover_artifact_catalogs",
    "encode_header",
    "read_artifact_header",
    "reset_artifact_discovery",
    "stage_artifact",
]
