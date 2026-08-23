"""Typed programmatic builder for pipeline definition documents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from provium import Artifact, ArtifactDefinition, JsonValue, ProcedureDefinition
from provium.procedure.io import ProcedureIOField

from .models import (
    PipelineDefinition,
    PipelineInputCardinality,
    PipelineInputDefinition,
    PipelineInputScope,
    PipelineMetadata,
    PipelineNodeDefinition,
)
from .references import NodeOutputReference, PipelineInputReference, ReferenceValue


@dataclass(frozen=True, slots=True)
class PipelineInputHandle[ArtifactT: Artifact[Any, Any]]:
    """Typed handle for one scalar pipeline input."""

    _owner: object
    name: str
    artifact: ArtifactDefinition[ArtifactT]

    @property
    def reference(self) -> PipelineInputReference:
        return PipelineInputReference(name=self.name)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class PipelineRepeatedInputHandle[ArtifactT: Artifact[Any, Any]]:
    """Typed handle for one repeated pipeline input."""

    _owner: object
    name: str
    artifact: ArtifactDefinition[ArtifactT]

    @property
    def reference(self) -> PipelineInputReference:
        return PipelineInputReference(name=self.name)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class NodeOutputHandle[ArtifactT: Artifact[Any, Any]]:
    """Typed handle for one procedure-node output field."""

    _owner: object
    node: str
    field: str
    artifact: ArtifactDefinition[ArtifactT]

    @property
    def reference(self) -> NodeOutputReference:
        return NodeOutputReference(node=self.node, output=self.field)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class PipelineNodeHandle:
    """Handle for a node registered with one builder."""

    _owner: object
    node: str
    procedure: ProcedureDefinition[Any]

    def output(self, field: str) -> NodeOutputHandle[Any]:
        contract = self.procedure.resolve_contract()
        outputs = contract.Outputs
        if outputs is None or field not in outputs.fields:
            raise ValueError(
                f"procedure {self.procedure.identifier!r} has no output field {field!r}"
            )
        artifact = outputs.fields[field].artifact
        return NodeOutputHandle(self._owner, self.node, field, artifact)


ScalarHandle = PipelineInputHandle[Any] | NodeOutputHandle[Any]
BindingHandle = ScalarHandle | PipelineRepeatedInputHandle[Any] | Sequence[ScalarHandle]


class PipelineBuilder:
    """Build a validated PipelineDefinition from typed lightweight handles."""

    def __init__(
        self,
        *,
        identifier: str,
        version: str,
        label: str | None = None,
        description: str | None = None,
    ) -> None:
        self._owner = object()
        self._metadata = PipelineMetadata(
            identifier=identifier,
            version=version,
            label=label,
            description=description,
        )
        self._inputs: dict[str, PipelineInputDefinition] = {}
        self._nodes: dict[str, PipelineNodeDefinition] = {}
        self._outputs: dict[str, NodeOutputReference] = {}

    def record_input[ArtifactT: Artifact[Any, Any]](
        self,
        name: str,
        artifact: ArtifactDefinition[ArtifactT],
        *,
        optional: bool = False,
    ) -> PipelineInputHandle[ArtifactT]:
        minimum = 0 if optional else 1
        self._register_input(
            name,
            artifact,
            PipelineInputScope.RECORD,
            PipelineInputCardinality(minimum=minimum, maximum=1),
        )
        return PipelineInputHandle(self._owner, name, artifact)

    def repeated_record_input[ArtifactT: Artifact[Any, Any]](
        self,
        name: str,
        artifact: ArtifactDefinition[ArtifactT],
        *,
        minimum: int = 0,
        maximum: int | None = None,
    ) -> PipelineRepeatedInputHandle[ArtifactT]:
        self._register_input(
            name,
            artifact,
            PipelineInputScope.RECORD,
            PipelineInputCardinality(minimum=minimum, maximum=maximum),
        )
        return PipelineRepeatedInputHandle(self._owner, name, artifact)

    def shared_input[ArtifactT: Artifact[Any, Any]](
        self,
        name: str,
        artifact: ArtifactDefinition[ArtifactT],
        *,
        optional: bool = False,
    ) -> PipelineInputHandle[ArtifactT]:
        minimum = 0 if optional else 1
        self._register_input(
            name,
            artifact,
            PipelineInputScope.SHARED,
            PipelineInputCardinality(minimum=minimum, maximum=1),
        )
        return PipelineInputHandle(self._owner, name, artifact)

    def _register_input(
        self,
        name: str,
        artifact: ArtifactDefinition[Any],
        scope: PipelineInputScope,
        cardinality: PipelineInputCardinality,
    ) -> None:
        self._require_unique(name, self._inputs, "input")
        self._inputs[name] = PipelineInputDefinition(
            artifact=artifact.identifier,
            scope=scope,
            cardinality=cardinality,
        )

    def node(
        self,
        name: str,
        procedure: ProcedureDefinition[Any],
        *,
        setup: Mapping[str, BindingHandle] | None = None,
        inputs: Mapping[str, BindingHandle] | None = None,
        config: Mapping[str, JsonValue] | None = None,
        cache: str = "enabled",
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> PipelineNodeHandle:
        self._require_unique(name, self._nodes, "node")
        contract = procedure.resolve_contract()
        setup_bindings = setup or {}
        input_bindings = inputs or {}
        empty_fields: Mapping[str, ProcedureIOField] = {}
        setup_fields = (
            contract.SetupInputs.fields
            if contract.SetupInputs is not None
            else empty_fields
        )
        input_fields = (
            contract.Inputs.fields if contract.Inputs is not None else empty_fields
        )
        self._validate_contract_bindings("setup", setup_bindings, setup_fields)
        self._validate_contract_bindings("input", input_bindings, input_fields)
        self._nodes[name] = PipelineNodeDefinition.model_validate(
            {
                "uses": procedure.identifier,
                "setup": self._bindings(setup or {}),
                "inputs": self._bindings(inputs or {}),
                "config": dict(config or {}),
                "cache": cache,
                "metadata": dict(metadata or {}),
            }
        )
        return PipelineNodeHandle(self._owner, name, procedure)

    def output(self, name: str, handle: NodeOutputHandle[Any]) -> None:
        self._require_unique(name, self._outputs, "output")
        self._require_owner(handle)
        self._outputs[name] = handle.reference

    def build(self) -> PipelineDefinition:
        return PipelineDefinition(
            pipeline=self._metadata,
            inputs=dict(self._inputs),
            nodes=dict(self._nodes),
            outputs=dict(self._outputs),
        )

    def _validate_contract_bindings(
        self,
        group: str,
        bindings: Mapping[str, BindingHandle],
        fields: Mapping[str, ProcedureIOField],
    ) -> None:
        for name in bindings:
            if name not in fields:
                raise ValueError(f"unknown {group} field {name!r}")
        for name, field in fields.items():
            if field.required and name not in bindings:
                raise ValueError(f"missing required {group} field {name!r}")
        for name, value in bindings.items():
            field = fields[name]
            binding_is_repeated = isinstance(value, (list, PipelineRepeatedInputHandle))
            if field.repeated != binding_is_repeated:
                expected = "repeated" if field.repeated else "scalar"
                raise ValueError(
                    f"{group} field {name!r} requires a {expected} binding"
                )
            values: Sequence[ScalarHandle | PipelineRepeatedInputHandle[Any]]
            if isinstance(value, Sequence):
                values = value
            else:
                values = (value,)
            for handle in values:
                if handle.artifact.identifier != field.artifact.identifier:
                    raise ValueError(
                        f"{group} field {name!r} requires artifact "
                        f"{field.artifact.identifier!r}, got "
                        f"{handle.artifact.identifier!r}"
                    )

    def _bindings(
        self, bindings: Mapping[str, BindingHandle]
    ) -> dict[str, ReferenceValue]:
        return {name: self._binding(value) for name, value in bindings.items()}

    def _binding(self, value: BindingHandle) -> ReferenceValue:
        if isinstance(
            value,
            (PipelineInputHandle, PipelineRepeatedInputHandle, NodeOutputHandle),
        ):
            self._require_owner(value)
            return value.reference
        references: list[PipelineInputReference | NodeOutputReference] = []
        for handle in value:
            self._require_owner(handle)
            references.append(handle.reference)
        return tuple(references)

    def _require_owner(self, handle: object) -> None:
        owner = getattr(handle, "_owner", None)
        if owner is not self._owner:
            raise ValueError("handle belongs to a different PipelineBuilder")

    @staticmethod
    def _require_unique(name: str, values: Mapping[str, object], category: str) -> None:
        if name in values:
            raise ValueError(f"pipeline {category} {name!r} is already declared")


__all__ = [
    "NodeOutputHandle",
    "PipelineBuilder",
    "PipelineInputHandle",
    "PipelineNodeHandle",
    "PipelineRepeatedInputHandle",
]
