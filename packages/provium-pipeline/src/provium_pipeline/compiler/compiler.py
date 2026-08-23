"""Deterministic compilation of validated pipeline definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from provium import JsonValue, ProcedureIOFieldMetadata, canonical_digest

from ..definition import (
    NodeOutputReference,
    PipelineInputReference,
    canonical_definition_document,
)
from .catalogs import (
    ArtifactCatalogCollection,
    CatalogResolutionError,
    ProcedureCatalogCollection,
)
from .diagnostics import PipelineCompilationDiagnostic, PipelineCompilationError
from .models import (
    CompiledBindingPlan,
    CompiledOutputContract,
    CompiledPipeline,
    CompiledPipelineInput,
    CompiledPipelineNode,
    CompiledPipelineOutput,
)
from .resolution import PipelineConfigurationLayer, resolve_pipeline_configuration

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..definition import PipelineDefinition, PipelineNodeDefinition

    BindingReference = PipelineInputReference | NodeOutputReference
    BindingValue = BindingReference | tuple[BindingReference, ...]


@dataclass(frozen=True, slots=True)
class PipelineCompiler:
    """Compile a pipeline definition into an immutable deterministic plan."""

    artifact_catalogs: ArtifactCatalogCollection
    procedure_catalogs: ProcedureCatalogCollection

    def compile(
        self,
        definition: PipelineDefinition,
        *,
        configuration_layers: Sequence[PipelineConfigurationLayer] = (),
    ) -> CompiledPipeline:
        _validate_procedures(definition, self.procedure_catalogs)
        definition_document = canonical_definition_document(definition)
        definition_digest = canonical_digest(definition_document)
        resolved_configuration = resolve_pipeline_configuration(
            definition, self.procedure_catalogs, configuration_layers
        )
        resolved_nodes = {node.node: node for node in resolved_configuration.nodes}

        inputs = tuple(
            CompiledPipelineInput(
                name=name,
                artifact_identifier=str(item.artifact),
                scope=item.scope,
                minimum=item.cardinality.minimum,
                maximum=item.cardinality.maximum,
            )
            for name, item in sorted(definition.inputs.items())
        )
        for item in inputs:
            self.artifact_catalogs.resolve(item.artifact_identifier)

        ordered_names = _topological_order(definition)
        nodes: list[CompiledPipelineNode] = []
        for name in ordered_names:
            node = definition.nodes[name]
            contract = self.procedure_catalogs.resolve(str(node.uses))
            metadata = contract.resolve_contract().metadata
            setup_bindings = _compile_bindings(metadata.setup_inputs, node.setup)
            input_bindings = _compile_bindings(metadata.inputs, node.inputs)
            output_contracts = tuple(
                _compile_output_contract(field) for field in metadata.outputs
            )
            for output in output_contracts:
                self.artifact_catalogs.resolve(output.artifact_identifier)
            configuration = resolved_nodes[name].snapshot
            preparation_document: dict[str, JsonValue] = {
                "procedure_contract_digest": metadata.digest,
                "configuration": None if configuration is None else configuration.value,
                "setup_bindings": [_binding_document(item) for item in setup_bindings],
            }
            output_document: list[JsonValue] = [
                _output_contract_document(item) for item in output_contracts
            ]
            nodes.append(
                CompiledPipelineNode(
                    identifier=name,
                    procedure_identifier=str(node.uses),
                    procedure_contract_digest=metadata.digest,
                    configuration_snapshot=configuration,
                    setup_bindings=setup_bindings,
                    input_bindings=input_bindings,
                    output_contracts=output_contracts,
                    cache_policy=node.cache,
                    preparation_contract_digest=canonical_digest(preparation_document),
                    output_contract_digest=canonical_digest(output_document),
                )
            )

        compiled_by_name = {node.identifier: node for node in nodes}
        outputs = tuple(
            CompiledPipelineOutput(
                name=name,
                node=reference.node,
                field=reference.output,
                artifact_identifier=_output_by_name(
                    compiled_by_name[reference.node], reference.output
                ).artifact_identifier,
                contract_digest=_output_by_name(
                    compiled_by_name[reference.node], reference.output
                ).digest,
            )
            for name, reference in sorted(definition.outputs.items())
        )
        semantic_document: dict[str, JsonValue] = {
            "inputs": [
                {
                    "artifact_identifier": item.artifact_identifier,
                    "scope": item.scope,
                    "minimum": item.minimum,
                    "maximum": item.maximum,
                }
                for item in inputs
            ],
            "nodes": [_semantic_node_document(item, ordered_names) for item in nodes],
            "outputs": [
                {
                    "node": ordered_names.index(item.node),
                    "field": item.field,
                    "artifact_identifier": item.artifact_identifier,
                    "contract_digest": item.contract_digest,
                }
                for item in outputs
            ],
        }
        return CompiledPipeline(
            identifier=str(definition.pipeline.identifier),
            version=str(definition.pipeline.version),
            definition_snapshot=definition_document,
            definition_digest=definition_digest,
            semantic_digest=canonical_digest(semantic_document),
            inputs=inputs,
            nodes=tuple(nodes),
            outputs=outputs,
            resolved_configuration=resolved_configuration,
        )


def _validate_procedures(
    definition: PipelineDefinition,
    catalogs: ProcedureCatalogCollection,
) -> None:
    diagnostics: list[PipelineCompilationDiagnostic] = []
    for name, node in sorted(definition.nodes.items()):
        try:
            catalogs.resolve(str(node.uses))
        except CatalogResolutionError:
            diagnostics.append(
                PipelineCompilationDiagnostic(
                    code="unknown-procedure",
                    path=f"nodes.{name}.uses",
                    message=f"procedure '{node.uses}' is not registered",
                )
            )
    if diagnostics:
        raise PipelineCompilationError(tuple(diagnostics))


def _topological_order(definition: PipelineDefinition) -> tuple[str, ...]:
    remaining = set(definition.nodes)
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            name
            for name in remaining
            if _node_dependencies(definition.nodes[name]).issubset(ordered)
        )
        if not ready:
            raise ValueError("pipeline graph contains a cycle")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return tuple(ordered)


def _node_dependencies(node: PipelineNodeDefinition) -> set[str]:
    references = [*node.setup.values(), *node.inputs.values()]
    return {
        reference.node
        for value in references
        for reference in (value if isinstance(value, tuple) else (value,))
        if isinstance(reference, NodeOutputReference)
    }


def _compile_bindings(
    fields: tuple[ProcedureIOFieldMetadata, ...],
    bindings: Mapping[str, BindingValue],
) -> tuple[CompiledBindingPlan, ...]:
    result: list[CompiledBindingPlan] = []
    for field in fields:
        value = bindings.get(field.name)
        values = (
            () if value is None else value if isinstance(value, tuple) else (value,)
        )
        result.append(
            CompiledBindingPlan(
                field=field.name,
                artifact_identifier=field.artifact_identifier,
                minimum=field.minimum,
                maximum=field.maximum,
                references=tuple(_reference_text(reference) for reference in values),
            )
        )
    return tuple(result)


def _reference_text(reference: PipelineInputReference | NodeOutputReference) -> str:
    if isinstance(reference, PipelineInputReference):
        return f"$inputs.{reference.name}"
    return f"$nodes.{reference.node}.outputs.{reference.output}"


def _compile_output_contract(
    field: ProcedureIOFieldMetadata,
) -> CompiledOutputContract:
    document: dict[str, JsonValue] = {
        "field": field.name,
        "artifact_identifier": field.artifact_identifier,
        "minimum": field.minimum,
        "maximum": field.maximum,
    }
    return CompiledOutputContract(
        field=field.name,
        artifact_identifier=field.artifact_identifier,
        minimum=field.minimum,
        maximum=field.maximum,
        digest=canonical_digest(document),
    )


def _output_by_name(node: CompiledPipelineNode, field: str) -> CompiledOutputContract:
    return next(output for output in node.output_contracts if output.field == field)


def _binding_document(binding: CompiledBindingPlan) -> dict[str, JsonValue]:
    return {
        "field": binding.field,
        "artifact_identifier": binding.artifact_identifier,
        "minimum": binding.minimum,
        "maximum": binding.maximum,
        "references": list(binding.references),
    }


def _output_contract_document(
    output: CompiledOutputContract,
) -> dict[str, JsonValue]:
    return {
        "field": output.field,
        "artifact_identifier": output.artifact_identifier,
        "minimum": output.minimum,
        "maximum": output.maximum,
    }


def _semantic_node_document(
    node: CompiledPipelineNode, ordered_names: tuple[str, ...]
) -> dict[str, JsonValue]:
    def normalize(reference: str) -> str:
        if not reference.startswith("$nodes."):
            return reference
        _, name, _, field = reference.split(".", 3)
        return f"$nodes.{ordered_names.index(name)}.outputs.{field}"

    return {
        "procedure_identifier": node.procedure_identifier,
        "procedure_contract_digest": node.procedure_contract_digest,
        "configuration": None
        if node.configuration_snapshot is None
        else node.configuration_snapshot.value,
        "setup_bindings": [
            {
                **_binding_document(binding),
                "references": [normalize(r) for r in binding.references],
            }
            for binding in node.setup_bindings
        ],
        "input_bindings": [
            {
                **_binding_document(binding),
                "references": [normalize(r) for r in binding.references],
            }
            for binding in node.input_bindings
        ],
        "outputs": [
            _output_contract_document(output) for output in node.output_contracts
        ],
        "cache_policy": node.cache_policy,
    }
