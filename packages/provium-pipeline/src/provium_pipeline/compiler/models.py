"""Immutable compiled pipeline plan models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from provium import ConfigurationSnapshot, JsonValue

from ..definition import PipelineInputScope
from .resolution import ResolvedPipelineConfiguration


@dataclass(frozen=True, slots=True)
class CompiledPipelineInput:
    name: str
    artifact_identifier: str
    scope: PipelineInputScope
    minimum: int
    maximum: int | None


@dataclass(frozen=True, slots=True)
class CompiledBindingPlan:
    field: str
    artifact_identifier: str
    minimum: int
    maximum: int | None
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledOutputContract:
    field: str
    artifact_identifier: str
    minimum: int
    maximum: int | None
    digest: str


@dataclass(frozen=True, slots=True)
class CompiledPipelineNode:
    identifier: str
    procedure_identifier: str
    procedure_contract_digest: str
    configuration_snapshot: ConfigurationSnapshot | None
    setup_bindings: tuple[CompiledBindingPlan, ...]
    input_bindings: tuple[CompiledBindingPlan, ...]
    output_contracts: tuple[CompiledOutputContract, ...]
    cache_policy: Literal["enabled", "disabled"]
    preparation_contract_digest: str
    output_contract_digest: str


@dataclass(frozen=True, slots=True)
class CompiledPipelineOutput:
    name: str
    node: str
    field: str
    artifact_identifier: str
    contract_digest: str


@dataclass(frozen=True, slots=True)
class CompiledPipeline:
    identifier: str
    version: str
    definition_snapshot: JsonValue
    definition_digest: str
    semantic_digest: str
    inputs: tuple[CompiledPipelineInput, ...]
    nodes: tuple[CompiledPipelineNode, ...]
    outputs: tuple[CompiledPipelineOutput, ...]
    resolved_configuration: ResolvedPipelineConfiguration
