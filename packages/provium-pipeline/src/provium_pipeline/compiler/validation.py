"""Static validation for pipeline compilation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from provium import ProcedureContractMetadata, ProcedureIOFieldMetadata

from ..definition import (
    NodeOutputReference,
    PipelineDefinition,
    PipelineInputReference,
    canonical_definition_document,
)
from .catalogs import (
    ArtifactCatalogCollection,
    CatalogResolutionError,
    ProcedureCatalogCollection,
)
from .diagnostics import PipelineCompilationDiagnostic, PipelineCompilationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..definition import PipelineNodeDefinition

    BindingReference = PipelineInputReference | NodeOutputReference
    BindingValue = BindingReference | tuple[BindingReference, ...]


def _validate_definition_model(definition: PipelineDefinition) -> None:
    try:
        PipelineDefinition.model_validate(canonical_definition_document(definition))
    except ValidationError as error:
        diagnostics = tuple(
            _diagnostic(
                "invalid-definition",
                ".".join(str(part) for part in detail["loc"]),
                detail["msg"],
            )
            for detail in error.errors()
        )
        raise PipelineCompilationError(diagnostics) from error


def validate_pipeline_definition(
    definition: PipelineDefinition,
    artifact_catalogs: ArtifactCatalogCollection,
    procedure_catalogs: ProcedureCatalogCollection,
) -> None:
    """Raise one ordered error containing all statically knowable failures."""
    _validate_definition_model(definition)
    diagnostics: list[PipelineCompilationDiagnostic] = []
    contracts: dict[str, ProcedureContractMetadata] = {}

    for name, pipeline_input in sorted(definition.inputs.items()):
        _validate_artifact(
            str(pipeline_input.artifact),
            f"inputs.{name}.artifact",
            artifact_catalogs,
            diagnostics,
        )

    for name, node in sorted(definition.nodes.items()):
        try:
            procedure = procedure_catalogs.resolve(str(node.uses))
        except CatalogResolutionError:
            diagnostics.append(
                _diagnostic(
                    "unknown-procedure",
                    f"nodes.{name}.uses",
                    f"procedure '{node.uses}' is not registered",
                )
            )
            continue
        metadata = procedure.resolve_contract().metadata
        contracts[name] = metadata
        for group, fields in (
            ("setup", metadata.setup_inputs),
            ("inputs", metadata.inputs),
            ("outputs", metadata.outputs),
        ):
            for field in fields:
                _validate_artifact(
                    field.artifact_identifier,
                    f"nodes.{name}.{group}.{field.name}",
                    artifact_catalogs,
                    diagnostics,
                )
        _validate_binding_group(
            definition,
            name,
            "setup",
            node.setup,
            metadata.setup_inputs,
            contracts,
            procedure_catalogs,
            diagnostics,
        )
        _validate_binding_group(
            definition,
            name,
            "inputs",
            node.inputs,
            metadata.inputs,
            contracts,
            procedure_catalogs,
            diagnostics,
        )

    for output_name, reference in sorted(definition.outputs.items()):
        path = f"outputs.{output_name}"
        metadata = _node_contract(
            reference.node, definition, contracts, procedure_catalogs
        )
        if reference.node not in definition.nodes:
            diagnostics.append(
                _diagnostic(
                    "unknown-node",
                    path,
                    f"node '{reference.node}' does not exist",
                )
            )
            continue
        if metadata is None:
            continue
        if _field(metadata.outputs, reference.output) is None:
            diagnostics.append(
                _diagnostic(
                    "unknown-node-output",
                    path,
                    f"node '{reference.node}' has no output '{reference.output}'",
                )
            )

    if not diagnostics:
        diagnostics.extend(_graph_diagnostics(definition, contracts))
    if diagnostics:
        raise PipelineCompilationError(tuple(diagnostics))


def _graph_diagnostics(
    definition: PipelineDefinition,
    contracts: dict[str, ProcedureContractMetadata],
) -> list[PipelineCompilationDiagnostic]:
    dependencies = {
        name: {
            str(reference.node)
            for reference in _node_references(node)
            if isinstance(reference, NodeOutputReference)
        }
        for name, node in definition.nodes.items()
    }
    cycle = _find_cycle(dependencies)
    if cycle is not None:
        return [
            _diagnostic(
                "cycle",
                "nodes",
                f"pipeline graph contains a cycle: {' -> '.join(cycle)}",
            )
        ]

    reachable = {
        name
        for name, node in definition.nodes.items()
        if any(
            isinstance(reference, PipelineInputReference)
            for reference in _node_references(node)
        )
        or (
            name in contracts
            and not contracts[name].setup_inputs
            and not contracts[name].inputs
        )
    }
    changed = True
    while changed:
        changed = False
        for name, upstream in dependencies.items():
            if name not in reachable and upstream.intersection(reachable):
                reachable.add(name)
                changed = True
    return [
        _diagnostic(
            "unreachable-node",
            f"nodes.{name}",
            f"node '{name}' is not reachable from a pipeline input or source condition",
        )
        for name in sorted(set(definition.nodes).difference(reachable))
    ]


def _find_cycle(
    dependencies: dict[str, set[str]],
) -> tuple[str, ...] | None:
    path: list[str] = []
    positions: dict[str, int] = {}
    visited: set[str] = set()

    def visit(name: str) -> tuple[str, ...] | None:
        position = positions.get(name)
        if position is not None:
            return tuple((*path[position:], name))
        if name in visited:
            return None
        positions[name] = len(path)
        path.append(name)
        for dependency in sorted(dependencies[name]):
            cycle = visit(dependency)
            if cycle is not None:
                return cycle
        path.pop()
        del positions[name]
        visited.add(name)
        return None

    for name in sorted(dependencies):
        cycle = visit(name)
        if cycle is not None:
            return cycle
    return None


def _node_references(node: PipelineNodeDefinition) -> tuple[BindingReference, ...]:
    references: list[BindingReference] = []
    for value in (*node.setup.values(), *node.inputs.values()):
        references.extend(value if isinstance(value, tuple) else (value,))
    return tuple(references)


def _validate_binding_group(
    definition: PipelineDefinition,
    node_name: str,
    group: str,
    bindings: Mapping[str, BindingValue],
    fields: tuple[ProcedureIOFieldMetadata, ...],
    contracts: dict[str, ProcedureContractMetadata],
    procedure_catalogs: ProcedureCatalogCollection,
    diagnostics: list[PipelineCompilationDiagnostic],
) -> None:
    fields_by_name = {field.name: field for field in fields}
    for field in fields:
        if field.minimum > 0 and field.name not in bindings:
            diagnostics.append(
                _diagnostic(
                    "missing-binding",
                    f"nodes.{node_name}.{group}.{field.name}",
                    f"required field '{field.name}' is not bound",
                )
            )
    for binding_name, value in sorted(bindings.items()):
        path = f"nodes.{node_name}.{group}.{binding_name}"
        field = fields_by_name.get(binding_name)
        if field is None:
            diagnostics.append(
                _diagnostic(
                    "unknown-binding",
                    path,
                    f"field '{binding_name}' is not declared by the procedure",
                )
            )
            continue
        references = value if isinstance(value, tuple) else (value,)
        source_contracts: list[tuple[str, int, int | None]] = []
        for reference in references:
            source_contract = _reference_contract(
                reference,
                definition,
                contracts,
                procedure_catalogs,
                path,
                diagnostics,
            )
            if source_contract is None:
                continue
            source_contracts.append(source_contract)
            source_artifact = source_contract[0]
            if source_artifact != field.artifact_identifier:
                diagnostics.append(
                    _diagnostic(
                        "artifact-mismatch",
                        path,
                        f"expected '{field.artifact_identifier}' but reference "
                        f"provides '{source_artifact}'",
                    )
                )
        if len(source_contracts) == len(references) and not _cardinality_compatible(
            field,
            source_contracts,
            isinstance(value, tuple),
        ):
            diagnostics.append(
                _diagnostic(
                    "cardinality-mismatch",
                    path,
                    f"binding cardinality is incompatible with field '{field.name}'",
                )
            )


def _cardinality_compatible(
    field: ProcedureIOFieldMetadata,
    source_contracts: list[tuple[str, int, int | None]],
    ordered_list: bool,
) -> bool:
    source_minimum = sum(contract[1] for contract in source_contracts)
    source_maximum = (
        None
        if any(contract[2] is None for contract in source_contracts)
        else sum(contract[2] or 0 for contract in source_contracts)
    )
    source_repeated = source_maximum is None or source_maximum > 1
    if field.repeated:
        shape_compatible = ordered_list or source_repeated
    else:
        shape_compatible = not ordered_list and not source_repeated
    minimum_compatible = source_minimum >= field.minimum
    maximum_compatible = field.maximum is None or (
        source_maximum is not None and source_maximum <= field.maximum
    )
    return shape_compatible and minimum_compatible and maximum_compatible


def _reference_contract(
    reference: BindingReference,
    definition: PipelineDefinition,
    contracts: dict[str, ProcedureContractMetadata],
    procedure_catalogs: ProcedureCatalogCollection,
    path: str,
    diagnostics: list[PipelineCompilationDiagnostic],
) -> tuple[str, int, int | None] | None:
    if isinstance(reference, PipelineInputReference):
        pipeline_input = definition.inputs.get(reference.name)
        if pipeline_input is None:
            diagnostics.append(
                _diagnostic(
                    "unknown-pipeline-input",
                    path,
                    f"pipeline input '{reference.name}' does not exist",
                )
            )
            return None
        return (
            str(pipeline_input.artifact),
            pipeline_input.cardinality.minimum,
            pipeline_input.cardinality.maximum,
        )
    metadata = _node_contract(reference.node, definition, contracts, procedure_catalogs)
    if reference.node not in definition.nodes:
        diagnostics.append(
            _diagnostic(
                "unknown-node",
                path,
                f"node '{reference.node}' does not exist",
            )
        )
        return None
    if metadata is None:
        return None
    output = _field(metadata.outputs, reference.output)
    if output is None:
        diagnostics.append(
            _diagnostic(
                "unknown-node-output",
                path,
                f"node '{reference.node}' has no output '{reference.output}'",
            )
        )
        return None
    return output.artifact_identifier, output.minimum, output.maximum


def _node_contract(
    node_name: str,
    definition: PipelineDefinition,
    contracts: dict[str, ProcedureContractMetadata],
    procedure_catalogs: ProcedureCatalogCollection,
) -> ProcedureContractMetadata | None:
    if node_name not in definition.nodes:
        return None
    cached = contracts.get(node_name)
    if cached is not None:
        return cached
    try:
        return (
            procedure_catalogs.resolve(str(definition.nodes[node_name].uses))
            .resolve_contract()
            .metadata
        )
    except CatalogResolutionError:
        return None


def _validate_artifact(
    identifier: str,
    path: str,
    catalogs: ArtifactCatalogCollection,
    diagnostics: list[PipelineCompilationDiagnostic],
) -> None:
    try:
        catalogs.resolve(identifier)
    except CatalogResolutionError:
        diagnostics.append(
            _diagnostic(
                "unknown-artifact",
                path,
                f"artifact '{identifier}' is not registered",
            )
        )


def _field(
    fields: tuple[ProcedureIOFieldMetadata, ...], name: str
) -> ProcedureIOFieldMetadata | None:
    return next((field for field in fields if field.name == name), None)


def _diagnostic(code: str, path: str, message: str) -> PipelineCompilationDiagnostic:
    return PipelineCompilationDiagnostic(code=code, path=path, message=message)
