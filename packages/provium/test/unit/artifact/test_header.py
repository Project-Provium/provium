"""Tests for deterministic artifact container headers."""

import json
import struct
from datetime import datetime
from pathlib import Path

import pytest

from provium import (
    ArtifactHeader,
    ArtifactLineage,
    ArtifactRecord,
    ArtifactReference,
    ProcedureExecutionRecord,
    ProcedureRecord,
    decode_header,
    encode_header,
    read_artifact_header,
)
from provium.artifact.header import CONTAINER_VERSION, MAGIC, PREFIX_SIZE


def lineage() -> ArtifactLineage:
    reference = ArtifactReference("artifact-1", "example.ImageV1")
    execution = ProcedureExecutionRecord(
        "execution-1",
        ProcedureRecord("example.CreateV1", "contract-digest"),
        outputs=(reference,),
    )
    return ArtifactLineage.for_execution(
        execution,
        (ArtifactRecord(reference, "a" * 64, execution.identity),),
    )


def header(**changes: object) -> ArtifactHeader:
    values: dict[str, object] = {
        "artifact_identifier": "example.ImageV1",
        "artifact_identity": "artifact-1",
        "body_offset": 4096,
        "body_length": 128,
        "body_digest": "a" * 64,
        "lineage": lineage(),
    }
    values.update(changes)
    return ArtifactHeader(**values)  # type: ignore[arg-type]


def test_header_encoding_round_trips_every_field_canonically() -> None:
    expected = header()

    encoded = encode_header(expected)
    actual = decode_header(encoded)

    assert actual == expected
    assert actual.metadata_offset == PREFIX_SIZE
    assert actual.metadata_length == len(encoded) - PREFIX_SIZE
    metadata = encoded[PREFIX_SIZE:]
    assert metadata == json.dumps(
        json.loads(metadata),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert encode_header(expected) == encode_header(header())


def test_artifact_header_can_be_read_from_a_container_path(tmp_path: Path) -> None:
    expected = header()
    path = tmp_path / "artifact.provium"
    path.write_bytes(encode_header(expected) + b"unread body data")

    assert read_artifact_header(path) == expected


def test_read_artifact_header_rejects_a_truncated_prefix(tmp_path: Path) -> None:
    path = tmp_path / "truncated.provium"
    path.write_bytes(MAGIC)

    with pytest.raises(ValueError, match="truncated fixed header"):
        read_artifact_header(path)


def test_fixed_prefix_defines_every_container_region() -> None:
    expected = header(body_offset=8192, body_length=1234)

    encoded = encode_header(expected)
    prefix = struct.unpack(">8sHQQQQ", encoded[:PREFIX_SIZE])

    assert prefix == (
        MAGIC,
        CONTAINER_VERSION,
        expected.metadata_offset,
        expected.metadata_length,
        expected.body_offset,
        expected.body_length,
    )
    metadata = json.loads(
        encoded[
            expected.metadata_offset : expected.metadata_offset
            + expected.metadata_length
        ]
    )
    assert "body_offset" not in metadata
    assert "body_length" not in metadata


def test_header_supports_empty_large_and_relocated_bodies() -> None:
    empty = header(body_offset=PREFIX_SIZE + 1000, body_length=0)
    large = header(body_offset=2**48 + 123, body_length=2**40 + 456)
    relocated = header(metadata_offset=PREFIX_SIZE + 32)

    assert decode_header(encode_header(empty)) == empty
    assert decode_header(encode_header(large)) == large
    encoded = encode_header(relocated)
    assert encoded[PREFIX_SIZE : relocated.metadata_offset] == bytes(32)
    assert decode_header(encoded) == relocated


def test_header_can_place_the_body_immediately_after_variable_metadata() -> None:
    reference = ArtifactReference("artifact-large", "example.LargeV1")
    execution = ProcedureExecutionRecord(
        "execution-large",
        ProcedureRecord(
            "example.CreateV1",
            "contract-digest",
            config={"payload": "x" * 10_000},
            config_codec="json",
        ),
        outputs=(reference,),
    )
    digest = "b" * 64
    large_lineage = ArtifactLineage.for_execution(
        execution,
        (ArtifactRecord(reference, digest, execution.identity),),
    )

    expected = ArtifactHeader.create(
        artifact_identifier=reference.artifact_identifier,
        artifact_identity=reference.identity,
        body_length=123,
        body_digest=digest,
        lineage=large_lineage,
    )

    assert expected.body_offset == expected.metadata_offset + expected.metadata_length
    assert expected.body_offset > 4096
    assert decode_header(encode_header(expected)) == expected


def test_decode_rejects_invalid_prefix_values() -> None:
    encoded = bytearray(encode_header(header()))
    encoded[: len(MAGIC)] = b"BADMAGIC"
    with pytest.raises(ValueError, match="magic"):
        decode_header(encoded)

    encoded = bytearray(encode_header(header()))
    struct.pack_into(">H", encoded, len(MAGIC), CONTAINER_VERSION + 1)
    with pytest.raises(ValueError, match="version"):
        decode_header(encoded)

    encoded = bytearray(encode_header(header()))
    struct.pack_into(">Q", encoded, len(MAGIC) + 2, PREFIX_SIZE - 1)
    with pytest.raises(ValueError, match="metadata offset"):
        decode_header(encoded)


@pytest.mark.parametrize("length", [0, 1, PREFIX_SIZE - 1])
def test_decode_rejects_a_truncated_fixed_header(length: int) -> None:
    with pytest.raises(ValueError, match="fixed header"):
        decode_header(encode_header(header())[:length])


def test_decode_rejects_truncated_or_malformed_metadata() -> None:
    encoded = encode_header(header())
    with pytest.raises(ValueError, match="truncated metadata"):
        decode_header(encoded[:-1])

    for metadata in (b"not-json", b"[]", b'{"body_offset":1}'):
        prefix = struct.pack(
            ">8sHQQQQ",
            MAGIC,
            CONTAINER_VERSION,
            PREFIX_SIZE,
            len(metadata),
            4096,
            128,
        )
        with pytest.raises(ValueError, match="metadata"):
            decode_header(prefix + metadata)


def test_decode_rejects_a_nonstring_creation_time() -> None:
    encoded = encode_header(ArtifactHeader.create(
        artifact_identifier="example.ImageV1",
        artifact_identity="artifact-1",
        body_length=128,
        body_digest="a" * 64,
        lineage=lineage(),
    ))
    metadata = json.loads(encoded[PREFIX_SIZE:])
    metadata["created_at"] = 123
    malformed = json.dumps(
        metadata,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    prefix = struct.pack(
        ">8sHQQQQ",
        MAGIC,
        CONTAINER_VERSION,
        PREFIX_SIZE,
        len(malformed),
        4096,
        128,
    )

    with pytest.raises(ValueError, match="malformed artifact metadata"):
        decode_header(prefix + malformed)


def test_decode_rejects_noncanonical_metadata_encoding() -> None:
    encoded = encode_header(header())
    metadata = encoded[PREFIX_SIZE:] + b" "
    prefix = struct.pack(
        ">8sHQQQQ",
        MAGIC,
        CONTAINER_VERSION,
        PREFIX_SIZE,
        len(metadata),
        4096,
        128,
    )

    with pytest.raises(ValueError, match="metadata encoding"):
        decode_header(prefix + metadata)

    decoded = json.loads(encoded[PREFIX_SIZE:])
    reordered = json.dumps(
        dict(reversed(tuple(decoded.items()))),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    assert len(reordered) == len(encoded[PREFIX_SIZE:])
    with pytest.raises(ValueError, match="metadata encoding"):
        decode_header(encoded[:PREFIX_SIZE] + reordered)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"artifact_identifier": ""}, "artifact_identifier"),
        ({"artifact_identifier": object()}, "artifact_identifier"),
        ({"artifact_identity": ""}, "artifact_identity"),
        ({"body_offset": -1}, "body_offset"),
        ({"body_offset": True}, "body_offset"),
        ({"body_offset": 2**64}, "body_offset"),
        ({"body_length": -1}, "body_length"),
        ({"body_length": True}, "body_length"),
        ({"body_length": 2**64}, "body_length"),
        ({"body_digest": ""}, "body_digest"),
        ({"lineage": object()}, "lineage"),
        ({"created_at": object()}, "created_at"),
        ({"created_at": datetime(2026, 1, 1)}, "timezone-aware"),
        ({"metadata_offset": PREFIX_SIZE - 1}, "metadata_offset"),
        ({"metadata_offset": True}, "metadata_offset"),
        ({"metadata_offset": 2**64}, "metadata_offset"),
    ],
)
def test_header_validates_fields(changes: dict[str, object], message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        header(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_identifier": "example.OtherV1"},
        {"artifact_identity": "other-artifact"},
        {"body_digest": "b" * 64},
    ],
)
def test_header_must_match_its_lineage_record(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="lineage"):
        header(**changes)


def test_header_rejects_a_body_that_overlaps_its_metadata() -> None:
    with pytest.raises(ValueError, match="body_offset.*metadata"):
        header(body_offset=PREFIX_SIZE)


def test_encode_requires_an_artifact_header() -> None:
    with pytest.raises(TypeError, match="ArtifactHeader"):
        encode_header(object())  # type: ignore[arg-type]
