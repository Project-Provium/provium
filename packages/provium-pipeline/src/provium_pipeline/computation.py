"""Stable semantic identities for procedure computations."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from provium import JsonValue, canonical_digest, canonical_json
from provium_pipeline.identifiers import ComputationKey


@dataclass(frozen=True, slots=True)
class ArtifactFingerprint:
    """The artifact properties that can affect a computation result."""

    artifact_identifier: str
    artifact_identity: str
    body_digest: str

    def to_document(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-domain representation."""
        return {
            "artifact_identifier": self.artifact_identifier,
            "artifact_identity": self.artifact_identity,
            "body_digest": self.body_digest,
        }


@dataclass(frozen=True, slots=True, init=False)
class SemanticInput:
    """One declared input and its ordered artifacts, or explicit absence."""

    field: str
    artifacts: tuple[ArtifactFingerprint, ...] | None

    def __init__(
        self,
        field: str,
        artifacts: Sequence[ArtifactFingerprint] | None,
    ) -> None:
        object.__setattr__(self, "field", field)
        immutable_artifacts = None if artifacts is None else tuple(artifacts)
        object.__setattr__(self, "artifacts", immutable_artifacts)

    def to_document(self) -> dict[str, JsonValue]:
        """Return an order-preserving JSON-domain representation."""
        artifacts: JsonValue
        if self.artifacts is None:
            artifacts = None
        else:
            artifacts = [artifact.to_document() for artifact in self.artifacts]
        return {"field": self.field, "artifacts": artifacts}


@dataclass(frozen=True, slots=True, init=False)
class ComputationRequest:
    """All and only the semantic values used to identify a computation."""

    procedure_identifier: str
    procedure_contract_digest: str
    _configuration_document: str
    configuration_schema_digest: str | None
    setup_inputs: tuple[SemanticInput, ...]
    inputs: tuple[SemanticInput, ...]
    output_contract_digest: str

    def __init__(
        self,
        *,
        procedure_identifier: str,
        procedure_contract_digest: str,
        configuration: JsonValue,
        configuration_schema_digest: str | None,
        setup_inputs: Sequence[SemanticInput],
        inputs: Sequence[SemanticInput],
        output_contract_digest: str,
    ) -> None:
        object.__setattr__(self, "procedure_identifier", procedure_identifier)
        object.__setattr__(
            self, "procedure_contract_digest", procedure_contract_digest
        )
        object.__setattr__(
            self, "_configuration_document", canonical_json(configuration)
        )
        object.__setattr__(
            self, "configuration_schema_digest", configuration_schema_digest
        )
        object.__setattr__(self, "setup_inputs", tuple(setup_inputs))
        object.__setattr__(self, "inputs", tuple(inputs))
        object.__setattr__(self, "output_contract_digest", output_contract_digest)

    @property
    def configuration(self) -> JsonValue:
        """Return a detached copy of the canonical configuration value."""
        return cast(JsonValue, json.loads(self._configuration_document))


@dataclass(frozen=True, slots=True)
class ComputationIdentity:
    """A computation key paired with its immutable canonical document."""

    key: ComputationKey
    _canonical_document: str

    @property
    def document(self) -> dict[str, JsonValue]:
        """Return a detached copy of the computation document."""
        value = cast(JsonValue, json.loads(self._canonical_document))
        if not isinstance(value, dict):  # pragma: no cover - construction invariant
            raise TypeError("computation document must be an object")
        return value


def computation_identity(request: ComputationRequest) -> ComputationIdentity:
    """Build the stable identity of a procedure invocation's semantics."""
    document: dict[str, JsonValue] = {
        "schema": "provium.computation/v1",
        "procedure_definition": request.procedure_identifier,
        "procedure_contract_digest": request.procedure_contract_digest,
        "configuration": request.configuration,
        "configuration_schema_digest": request.configuration_schema_digest,
        "setup_inputs": [item.to_document() for item in request.setup_inputs],
        "inputs": [item.to_document() for item in request.inputs],
        "output_contract_digest": request.output_contract_digest,
    }
    return ComputationIdentity(
        key=ComputationKey(canonical_digest(document)),
        _canonical_document=canonical_json(document),
    )
