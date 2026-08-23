"""Binary container prefix and deterministic artifact metadata encoding."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any, Self, cast

from provium.provenance import ArtifactLineage, ArtifactReference

MAGIC = b"PROVIUM\0"
CONTAINER_VERSION = 1
_PREFIX = struct.Struct(">8sHQQQQ")
PREFIX_SIZE = _PREFIX.size
_UINT64_MAX = 2**64 - 1
_LEGACY_METADATA_KEYS = {
    "artifact_identifier",
    "artifact_identity",
    "body_digest",
    "lineage",
}
_METADATA_KEYS = _LEGACY_METADATA_KEYS | {"created_at"}


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_uint64(value: object, field_name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    if value > _UINT64_MAX:
        raise ValueError(f"{field_name} exceeds the container format limit")


def _metadata_bytes(header: ArtifactHeader) -> bytes:
    metadata: dict[str, Any] = {
        "artifact_identifier": header.artifact_identifier,
        "artifact_identity": header.artifact_identity,
        "body_digest": header.body_digest,
        "lineage": header.lineage.to_dict(),
    }
    if header.created_at is not None:
        metadata["created_at"] = header.created_at.isoformat().replace("+00:00", "Z")
    return json.dumps(
        metadata,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ArtifactHeader:
    """Metadata required to locate and identify one artifact body."""

    artifact_identifier: str
    artifact_identity: str
    body_offset: int
    body_length: int
    body_digest: str
    lineage: ArtifactLineage
    created_at: datetime | None = None
    metadata_offset: int = PREFIX_SIZE
    metadata_length: int = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        artifact_identifier: str,
        artifact_identity: str,
        body_length: int,
        body_digest: str,
        lineage: ArtifactLineage,
        created_at: datetime | None = None,
        metadata_offset: int = PREFIX_SIZE,
    ) -> Self:
        """Create a header with its body directly after encoded metadata."""
        created_at = created_at or datetime.now(UTC)
        sizing_header = cls(
            artifact_identifier=artifact_identifier,
            artifact_identity=artifact_identity,
            body_offset=_UINT64_MAX,
            body_length=body_length,
            body_digest=body_digest,
            lineage=lineage,
            created_at=created_at,
            metadata_offset=metadata_offset,
        )
        return cls(
            artifact_identifier=artifact_identifier,
            artifact_identity=artifact_identity,
            body_offset=metadata_offset + sizing_header.metadata_length,
            body_length=body_length,
            body_digest=body_digest,
            lineage=lineage,
            created_at=created_at,
            metadata_offset=metadata_offset,
        )

    def __post_init__(self) -> None:
        _require_text(self.artifact_identifier, "artifact_identifier")
        _require_text(self.artifact_identity, "artifact_identity")
        _require_uint64(self.body_offset, "body_offset")
        _require_uint64(self.body_length, "body_length")
        _require_text(self.body_digest, "body_digest")
        if not isinstance(self.lineage, ArtifactLineage):
            raise TypeError("lineage must be an ArtifactLineage")
        if self.created_at is not None:
            if not isinstance(self.created_at, datetime):
                raise TypeError("created_at must be a datetime or None")
            if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
                raise ValueError("created_at must be timezone-aware")
            object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        self._validate_lineage_record()
        _require_uint64(self.metadata_offset, "metadata_offset")
        if self.metadata_offset < PREFIX_SIZE:
            raise ValueError("metadata_offset must not overlap the fixed header")
        metadata_length = len(_metadata_bytes(self))
        if self.body_offset < self.metadata_offset + metadata_length:
            raise ValueError("body_offset must not overlap artifact metadata")
        object.__setattr__(self, "metadata_length", metadata_length)

    def _validate_lineage_record(self) -> None:
        reference = ArtifactReference(
            self.artifact_identity,
            self.artifact_identifier,
        )
        try:
            record = self.lineage.artifact(reference)
        except (KeyError, ValueError) as error:
            raise ValueError("artifact header does not match its lineage") from error
        if record.body_digest != self.body_digest:
            raise ValueError("artifact header digest does not match its lineage")


def encode_header(header: ArtifactHeader) -> bytes:
    """Encode a fixed prefix and its canonical metadata."""
    if not isinstance(header, ArtifactHeader):
        raise TypeError("header must be an ArtifactHeader")
    metadata = _metadata_bytes(header)
    prefix = _PREFIX.pack(
        MAGIC,
        CONTAINER_VERSION,
        header.metadata_offset,
        len(metadata),
        header.body_offset,
        header.body_length,
    )
    padding = bytes(header.metadata_offset - PREFIX_SIZE)
    return prefix + padding + metadata


def _decode_metadata(
    metadata_bytes: bytes,
    metadata_offset: int,
    body_offset: int,
    body_length: int,
) -> ArtifactHeader:
    decoded: object = json.loads(metadata_bytes.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise TypeError("invalid metadata shape")
    metadata = cast(dict[str, Any], decoded)
    if set(metadata) not in (_LEGACY_METADATA_KEYS, _METADATA_KEYS):
        raise ValueError("invalid metadata shape")
    created_at_value = metadata.get("created_at")
    if created_at_value is None:
        created_at = None
    elif isinstance(created_at_value, str):
        created_at = datetime.fromisoformat(created_at_value.replace("Z", "+00:00"))
    else:
        raise TypeError("created_at must be a string")
    return ArtifactHeader(
        artifact_identifier=metadata["artifact_identifier"],
        artifact_identity=metadata["artifact_identity"],
        body_offset=body_offset,
        body_length=body_length,
        body_digest=metadata["body_digest"],
        lineage=ArtifactLineage.from_dict(metadata["lineage"]),
        created_at=created_at,
        metadata_offset=metadata_offset,
    )


def decode_header(data: bytes | bytearray | memoryview) -> ArtifactHeader:
    """Decode and validate a complete artifact header from bytes."""
    if len(data) < PREFIX_SIZE:
        raise ValueError("truncated fixed header")
    (
        magic,
        version,
        metadata_offset,
        metadata_length,
        body_offset,
        body_length,
    ) = _PREFIX.unpack_from(data)
    if magic != MAGIC:
        raise ValueError("invalid artifact magic bytes")
    if version != CONTAINER_VERSION:
        raise ValueError(f"unsupported container version: {version}")
    if metadata_offset < PREFIX_SIZE:
        raise ValueError("metadata offset overlaps the fixed header")
    metadata_end = metadata_offset + metadata_length
    if len(data) < metadata_end:
        raise ValueError("truncated metadata")
    metadata_bytes = bytes(data[metadata_offset:metadata_end])
    try:
        header = _decode_metadata(
            metadata_bytes,
            metadata_offset,
            body_offset,
            body_length,
        )
    except (
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise ValueError("malformed artifact metadata") from error
    if metadata_bytes != _metadata_bytes(header):
        raise ValueError("malformed artifact metadata encoding")
    return header


def read_artifact_header(path: str | PathLike[str]) -> ArtifactHeader:
    """Read an artifact container's header without reading its body."""
    with Path(path).open("rb") as stream:
        prefix = stream.read(PREFIX_SIZE)
        if len(prefix) < PREFIX_SIZE:
            raise ValueError("truncated fixed header")
        _, _, metadata_offset, metadata_length, _, _ = _PREFIX.unpack(prefix)
        metadata_end = metadata_offset + metadata_length
        stream.seek(0)
        return decode_header(stream.read(metadata_end))


__all__ = [
    "CONTAINER_VERSION",
    "MAGIC",
    "PREFIX_SIZE",
    "ArtifactHeader",
    "decode_header",
    "encode_header",
    "read_artifact_header",
]
