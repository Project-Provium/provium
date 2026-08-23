"""Identifier and canonical-value contracts."""

from __future__ import annotations

from uuid import UUID

import pytest

from provium import (
    canonical_digest as core_canonical_digest,
)
from provium import (
    canonical_json as core_canonical_json,
)
from provium import (
    canonical_json_bytes as core_canonical_json_bytes,
)
from provium_pipeline import (
    AttemptId,
    ComputationKey,
    DispatchId,
    InputRecordKey,
    InputSetIdentifier,
    PipelineIdentifier,
    PipelineInputName,
    PipelineNodeIdentifier,
    PipelineOutputName,
    PipelineVersion,
    RunId,
    StoreIdentifier,
    TaskId,
    canonical_digest,
    canonical_json,
    canonical_json_bytes,
)

TEXT_IDENTIFIERS = (
    PipelineIdentifier,
    PipelineVersion,
    PipelineNodeIdentifier,
    PipelineInputName,
    PipelineOutputName,
    InputRecordKey,
    InputSetIdentifier,
    StoreIdentifier,
)

UUID_IDENTIFIERS = (RunId, DispatchId, TaskId, AttemptId)


@pytest.mark.parametrize("identifier_type", TEXT_IDENTIFIERS)
def test_text_identifiers_are_validated_immutable_strings(
    identifier_type: type[str],
) -> None:
    identifier = identifier_type("tngo.session-analysis_1")

    assert isinstance(identifier, str)
    assert str(identifier) == "tngo.session-analysis_1"
    assert identifier_type(identifier) is identifier


@pytest.mark.parametrize("identifier_type", TEXT_IDENTIFIERS)
@pytest.mark.parametrize("value", ["", " ", " leading", "trailing ", "a/b", "a:b"])
def test_text_identifiers_reject_invalid_values(
    identifier_type: type[str], value: str
) -> None:
    with pytest.raises(ValueError, match="valid identifier"):
        identifier_type(value)


@pytest.mark.parametrize("identifier_type", UUID_IDENTIFIERS)
def test_runtime_identifiers_generate_and_parse_uuid_values(
    identifier_type: type[RunId],
) -> None:
    generated = identifier_type.new()
    parsed = identifier_type.parse(str(generated))

    assert isinstance(generated.value, UUID)
    assert parsed == generated
    assert hash(parsed) == hash(generated)


@pytest.mark.parametrize("identifier_type", UUID_IDENTIFIERS)
def test_runtime_identifier_types_are_not_interchangeable(
    identifier_type: type[RunId],
) -> None:
    value = UUID("12345678-1234-5678-1234-567812345678")

    assert (
        identifier_type(value) != RunId(value) if identifier_type is not RunId else True
    )


def test_text_identifiers_reject_nonstring_values() -> None:
    with pytest.raises(ValueError, match="valid identifier"):
        PipelineIdentifier(1)  # type: ignore[arg-type]


def test_runtime_identifiers_are_immutable_and_have_diagnostic_repr() -> None:
    identifier = RunId(UUID("12345678-1234-5678-1234-567812345678"))

    assert repr(identifier) == "RunId('12345678-1234-5678-1234-567812345678')"
    with pytest.raises(AttributeError, match="immutable"):
        identifier._value = UUID(int=0)  # type: ignore[misc]


def test_runtime_identifiers_reject_non_uuid_values() -> None:
    with pytest.raises(TypeError, match="value must be a UUID"):
        RunId("12345678-1234-5678-1234-567812345678")  # type: ignore[arg-type]


def test_runtime_identifiers_reject_invalid_uuid_text() -> None:
    with pytest.raises(ValueError):
        RunId.parse("not-a-uuid")


def test_computation_key_is_a_normalized_sha256_digest() -> None:
    key = ComputationKey("A" * 64)

    assert str(key) == "a" * 64


@pytest.mark.parametrize("value", ["", "abc", "g" * 64, "a" * 63, "a" * 65])
def test_computation_key_rejects_invalid_digests(value: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        ComputationKey(value)


def test_pipeline_canonical_functions_are_the_core_contract() -> None:
    assert canonical_json_bytes is core_canonical_json_bytes
    assert canonical_json is core_canonical_json
    assert canonical_digest is core_canonical_digest
