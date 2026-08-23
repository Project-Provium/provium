"""Strict immutable models for pipeline definition documents."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from provium import JsonValue

from .references import (
    NodeOutputReference,
    Reference,
    ReferenceValue,
    parse_reference,
    parse_reference_value,
)


class _DefinitionModel(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class PipelineInputScope(StrEnum):
    """Whether an input is bound per record or once per run."""

    RECORD = "record"
    SHARED = "shared"


class PipelineInputCardinality(_DefinitionModel):
    """Allowed number of artifact bindings for a pipeline input."""

    minimum: int = Field(default=1, ge=0)
    maximum: int | None = 1

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("maximum must be at least minimum")
        return self

    @property
    def required(self) -> bool:
        return self.minimum > 0

    @property
    def repeated(self) -> bool:
        return self.maximum is None or self.maximum > 1


class PipelineMetadata(_DefinitionModel):
    """Identity and presentation metadata for a pipeline."""

    identifier: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    label: str | None = None
    description: str | None = None


class PipelineInputDefinition(_DefinitionModel):
    """One record-scoped or shared pipeline input declaration."""

    artifact: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    scope: PipelineInputScope = PipelineInputScope.RECORD
    cardinality: PipelineInputCardinality = Field(
        default_factory=PipelineInputCardinality
    )


class PipelineNodeDefinition(_DefinitionModel):
    """One procedure node and its declarative bindings."""

    uses: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    setup: dict[str, ReferenceValue] = Field(default_factory=dict)
    inputs: dict[str, ReferenceValue] = Field(default_factory=dict)
    config: dict[str, JsonValue] = Field(default_factory=dict)
    cache: Literal["enabled", "disabled"] = "enabled"
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("setup", "inputs", mode="before")
    @classmethod
    def parse_bindings(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        bindings = cast(dict[str, object], value)
        return {
            name: _coerce_reference_value(binding) for name, binding in bindings.items()
        }


class PipelineDefinition(_DefinitionModel):
    """Complete schema-version-one pipeline definition document."""

    schema_version: Literal["provium.pipeline/v1"] = Field(
        default="provium.pipeline/v1",
        alias="schema",
        serialization_alias="schema",
    )
    pipeline: PipelineMetadata
    inputs: dict[str, PipelineInputDefinition]
    nodes: dict[str, PipelineNodeDefinition]
    outputs: dict[str, NodeOutputReference]

    @field_validator("outputs", mode="before")
    @classmethod
    def parse_outputs(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        outputs: dict[str, NodeOutputReference] = {}
        raw_outputs = cast(dict[str, object], value)
        for name, raw_reference in raw_outputs.items():
            if isinstance(raw_reference, NodeOutputReference):
                reference: Reference = raw_reference
            elif isinstance(raw_reference, str):
                reference = parse_reference(raw_reference)
            else:
                raise PydanticCustomError(
                    "pipeline_output_reference",
                    "pipeline output must reference a node output",
                )
            if not isinstance(reference, NodeOutputReference):
                raise PydanticCustomError(
                    "pipeline_output_reference",
                    "pipeline output must reference a node output",
                )
            outputs[name] = reference
        return outputs


def _coerce_reference_value(value: object) -> ReferenceValue:
    if isinstance(value, Reference):
        return value
    if isinstance(value, tuple):
        tuple_value = cast(tuple[object, ...], value)
        if all(isinstance(item, Reference) for item in tuple_value):
            return cast(tuple[Reference, ...], tuple_value)
    if isinstance(value, str):
        return parse_reference_value(value)
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        if all(isinstance(item, str) for item in sequence):
            strings = tuple(cast(str, item) for item in sequence)
            return parse_reference_value(strings)
    raise ValueError("binding must be a pipeline reference or ordered reference list")


__all__ = [
    "PipelineDefinition",
    "PipelineInputCardinality",
    "PipelineInputDefinition",
    "PipelineInputScope",
    "PipelineMetadata",
    "PipelineNodeDefinition",
]
