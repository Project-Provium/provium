"""Semantic composition and validation of pipeline configuration layers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import cast

from provium import (
    ConfigurationSnapshot,
    JsonValue,
    ProcedureConfig,
    compose_configuration,
    validate_procedure_configuration,
)

from ..canonical import canonical_digest
from ..definition import PipelineDefinition
from .catalogs import ProcedureCatalogCollection
from .configuration import PipelineConfiguration, canonical_configuration_document


class PipelineConfigurationResolutionError(ValueError):
    """Raised when pipeline configuration cannot be resolved semantically."""


@dataclass(frozen=True, slots=True)
class PipelineConfigurationLayer:
    """One labeled raw layer and its stable canonical identity."""

    source: str
    configuration: PipelineConfiguration
    document: dict[str, JsonValue] = field(init=False)
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        document = canonical_configuration_document(self.configuration)
        object.__setattr__(self, "document", document)
        object.__setattr__(self, "digest", canonical_digest(document))


@dataclass(frozen=True, slots=True)
class ResolvedNodeConfiguration:
    """Validated configuration snapshot and ordered contributing sources."""

    node: str
    source_layers: tuple[str, ...]
    snapshot: ConfigurationSnapshot | None


@dataclass(frozen=True, slots=True)
class ResolvedPipelineConfiguration:
    """Stored configuration audit sufficient for export and resumption."""

    layers: tuple[PipelineConfigurationLayer, ...]
    nodes: tuple[ResolvedNodeConfiguration, ...]
    document: dict[str, JsonValue]
    document_digest: str


def resolve_pipeline_configuration(
    definition: PipelineDefinition,
    procedure_catalogs: ProcedureCatalogCollection,
    layers: Iterable[PipelineConfigurationLayer] = (),
) -> ResolvedPipelineConfiguration:
    """Compose and validate every node without resolving concrete procedures."""
    layer_tuple = tuple(layers)
    _validate_layer_nodes(definition, layer_tuple)
    resolved_nodes = tuple(
        _resolve_node_configuration(
            name,
            definition.nodes[name].uses,
            definition.nodes[name].config,
            procedure_catalogs,
            layer_tuple,
        )
        for name in sorted(definition.nodes)
    )
    resolved_values: dict[str, JsonValue] = {
        node.node: node.snapshot.value if node.snapshot is not None else {}
        for node in resolved_nodes
    }
    document: dict[str, JsonValue] = {
        "schema": "provium.pipeline-config/v1",
        "nodes": resolved_values,
    }
    return ResolvedPipelineConfiguration(
        layers=layer_tuple,
        nodes=resolved_nodes,
        document=document,
        document_digest=canonical_digest(document),
    )


def _validate_layer_nodes(
    definition: PipelineDefinition,
    layers: tuple[PipelineConfigurationLayer, ...],
) -> None:
    known = set(definition.nodes)
    for layer in layers:
        unknown = sorted(set(layer.configuration.nodes) - known)
        if unknown:
            raise PipelineConfigurationResolutionError(
                f"configuration layer {layer.source!r} references unknown node "
                f"{unknown[0]!r}"
            )


def _resolve_node_configuration(
    node: str,
    procedure_identifier: str,
    inline: Mapping[str, JsonValue],
    procedure_catalogs: ProcedureCatalogCollection,
    layers: tuple[PipelineConfigurationLayer, ...],
) -> ResolvedNodeConfiguration:
    raw_layers: list[Mapping[str, object]] = []
    sources: list[str] = []
    if inline:
        raw_layers.append(cast(Mapping[str, object], inline))
        sources.append(f"pipeline node {node!r} inline config")
    for layer in layers:
        values = layer.configuration.nodes.get(node)
        if values is not None:
            raw_layers.append(cast(Mapping[str, object], values))
            sources.append(layer.source)
    values = compose_configuration(raw_layers)
    procedure = procedure_catalogs.resolve(procedure_identifier)
    contract = procedure.resolve_contract()
    configuration_type = cast(
        type[ProcedureConfig] | None, getattr(contract, "configuration")
    )
    if configuration_type is None:
        if values:
            raise PipelineConfigurationResolutionError(
                f"pipeline node {node!r} procedure {procedure_identifier!r} "
                "does not accept configuration"
            )
        return ResolvedNodeConfiguration(node, tuple(sources), None)
    configuration = validate_procedure_configuration(
        procedure_identifier,
        configuration_type,
        values,
        source_layers=sources,
    )
    return ResolvedNodeConfiguration(
        node,
        tuple(sources),
        ConfigurationSnapshot.from_configuration(configuration),
    )
