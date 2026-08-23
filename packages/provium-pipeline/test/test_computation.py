from __future__ import annotations

import inspect

from provium import JsonValue
from provium_pipeline.computation import (
    ArtifactFingerprint,
    ComputationIdentity,
    ComputationRequest,
    SemanticInput,
    computation_identity,
)
from provium_pipeline.identifiers import ComputationKey

_FINGERPRINT_A = ArtifactFingerprint(
    artifact_identifier="example.text/v1",
    artifact_identity="artifact-a",
    body_digest="a" * 64,
)
_FINGERPRINT_B = ArtifactFingerprint(
    artifact_identifier="example.text/v1",
    artifact_identity="artifact-b",
    body_digest="b" * 64,
)


def _identity(
    *,
    inputs: tuple[SemanticInput, ...] = (),
    configuration: JsonValue = None,
    configuration_schema_digest: str | None = None,
    output_contract_digest: str = "c" * 64,
) -> ComputationIdentity:
    return computation_identity(
        ComputationRequest(
            procedure_identifier="example.process/v1",
            procedure_contract_digest="d" * 64,
            configuration=configuration,
            configuration_schema_digest=configuration_schema_digest,
            setup_inputs=(SemanticInput("setup", (_FINGERPRINT_A,)),),
            inputs=inputs,
            output_contract_digest=output_contract_digest,
        )
    )


def test_computation_identity_builds_the_versioned_canonical_document() -> None:
    identity = _identity(
        inputs=(
            SemanticInput("required", (_FINGERPRINT_A,)),
            SemanticInput("optional", None),
            SemanticInput("repeated", (_FINGERPRINT_A, _FINGERPRINT_B)),
        ),
        configuration={"threshold": 2},
        configuration_schema_digest="e" * 64,
    )

    assert isinstance(identity.key, ComputationKey)
    assert identity.document == {
        "schema": "provium.computation/v1",
        "procedure_definition": "example.process/v1",
        "procedure_contract_digest": "d" * 64,
        "configuration": {"threshold": 2},
        "configuration_schema_digest": "e" * 64,
        "setup_inputs": [
            {
                "field": "setup",
                "artifacts": [
                    {
                        "artifact_identifier": "example.text/v1",
                        "artifact_identity": "artifact-a",
                        "body_digest": "a" * 64,
                    }
                ],
            }
        ],
        "inputs": [
            {
                "field": "required",
                "artifacts": [_FINGERPRINT_A.to_document()],
            },
            {"field": "optional", "artifacts": None},
            {
                "field": "repeated",
                "artifacts": [
                    _FINGERPRINT_A.to_document(),
                    _FINGERPRINT_B.to_document(),
                ],
            },
        ],
        "output_contract_digest": "c" * 64,
    }


def test_repeated_input_order_changes_the_computation_key() -> None:
    first = _identity(
        inputs=(SemanticInput("items", (_FINGERPRINT_A, _FINGERPRINT_B)),)
    )
    second = _identity(
        inputs=(SemanticInput("items", (_FINGERPRINT_B, _FINGERPRINT_A)),)
    )

    assert first.key != second.key


def test_optional_absence_is_distinct_from_an_empty_repeated_input() -> None:
    absent = _identity(inputs=(SemanticInput("items", None),))
    empty = _identity(inputs=(SemanticInput("items", ()),))

    assert absent.key != empty.key
    assert absent.document["inputs"] == [{"field": "items", "artifacts": None}]
    assert empty.document["inputs"] == [{"field": "items", "artifacts": []}]


def test_configuration_and_output_contract_are_semantic() -> None:
    base = _identity()
    configured = _identity(
        configuration={"threshold": 2},
        configuration_schema_digest="e" * 64,
    )
    changed_output = _identity(output_contract_digest="f" * 64)

    assert len({base.key, configured.key, changed_output.key}) == 3


def test_computation_identity_accepts_one_semantic_request() -> None:
    assert tuple(inspect.signature(computation_identity).parameters) == ("request",)


def test_operational_context_is_excluded_from_computation_request() -> None:
    fields = ComputationRequest.__dataclass_fields__

    assert {
        "pipeline",
        "node",
        "run",
        "record",
        "profile",
        "worker_count",
        "retries",
        "locations",
        "host",
        "cache_policy",
    }.isdisjoint(fields)
