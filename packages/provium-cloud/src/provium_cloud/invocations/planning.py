"""Adapt unnamed requests to the existing Provium pipeline planner."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from provium.procedure.config import ConfigurationSnapshot
from provium.procedure.discovery import discover_procedure_catalogs
from provium.provenance import ProcedureRecord
from provium_pipeline.planning import build_plan, select_targets

from provium_cloud.deployment import CloudDeployment, SchedulingOptions
from provium_cloud.invocations.models import InvocationPlan, PipelineGraph, TaskPlan


def prepare_pipeline(
    graph: PipelineGraph | Mapping[str, Any] | str | Path,
    deployment: CloudDeployment,
    *,
    targets: Sequence[str] | None = None,
    force: bool | Sequence[str] = False,
    overrides: SchedulingOptions | Mapping[str, object] | None = None,
) -> InvocationPlan:
    if isinstance(graph, (str, Path)):
        import yaml
        from provium_pipeline.documents import UniqueLoader

        with Path(graph).open(encoding="utf-8") as stream:
            graph = yaml.load(stream, Loader=UniqueLoader)
    graph = PipelineGraph.model_validate(graph)
    plan = build_plan(graph.pipeline())
    plan = select_targets(plan, list(graph.targets if targets is None else targets))
    selectors = list(force) if not isinstance(force, bool) else []
    available = {
        selector
        for name, node in plan.configuration.nodes.items()
        for selector in (
            f"nodes.{name}",
            f"procedures.{node.procedure}",
            *(f"tags.{tag}" for tag in node.tags),
        )
    }
    if set(selectors) - available:
        raise ValueError("unknown or unmatched force selector")
    tasks: dict[str, TaskPlan] = {}
    for name in plan.order:
        node = plan.configuration.nodes[name]
        contract = plan.procedures[name].resolve_contract()
        bindings = {
            "inputs": dict(node.inputs),
            "setup_inputs": dict(node.setup_inputs),
        }
        value_types: dict[str, str] = {}
        for scope in bindings:
            for field in getattr(contract.metadata, scope):
                bindings[scope].setdefault(field.name, None)
                if field.value_type is not None:
                    value_types[f"{scope}.{field.name}"] = field.value_type
        task_selectors = {
            f"nodes.{name}",
            f"procedures.{node.procedure}",
            *(f"tags.{tag}" for tag in node.tags),
        }
        tasks[name] = TaskPlan(
            procedure=node.procedure,
            record=plan.records[name].to_dict(),
            configuration=plan.configurations[name],
            inputs=cast(dict[str, str | None], bindings["inputs"]),
            setup_inputs=cast(dict[str, str | None], bindings["setup_inputs"]),
            value_types=value_types,
            output_types=plan.output_types[name],
            settings=deployment.resolve(
                procedure=node.procedure, node=name, tags=node.tags, overrides=overrides
            ),
            force=force is True or bool(task_selectors.intersection(selectors)),
        )
    projected = plan.configuration.model_dump(exclude={"name", "outputs"})
    return InvocationPlan(
        graph=PipelineGraph.model_validate(
            {**projected, "targets": plan.configuration.outputs}
        ),
        tasks=tasks,
        targets=plan.configuration.outputs,
    )


def prepare_procedure(
    procedure: str,
    deployment: CloudDeployment,
    *,
    configuration: Mapping[str, Any] | None = None,
    tags: Sequence[str] = (),
    force: bool = False,
    overrides: SchedulingOptions | Mapping[str, object] | None = None,
) -> InvocationPlan:
    definition = discover_procedure_catalogs().resolve(procedure)
    contract = definition.resolve_contract()
    inputs: dict[str, Any] = {}
    bindings: dict[str, dict[str, str]] = {"inputs": {}, "setup_inputs": {}}
    for scope in bindings:
        for field in getattr(contract.metadata, scope):
            name = field.name if scope == "inputs" else f"setup_{field.name}"
            if name in inputs:
                raise ValueError(f"conflicting standalone input name: {name}")
            inputs[name] = (
                {"value_type": field.value_type}
                if field.value_type
                else field.artifact_identifier
            )
            bindings[scope][field.name] = f"$inputs.{name}"
    graph = PipelineGraph.model_validate(
        {
            "inputs": inputs,
            "nodes": {
                "procedure": {
                    "procedure": procedure,
                    "configuration": dict(configuration or {}),
                    "tags": list(tags),
                    **bindings,
                }
            },
            "targets": {
                field.name: f"$nodes.procedure.{field.name}"
                for field in contract.metadata.outputs
            },
        }
    )
    for field in (
        *contract.metadata.inputs,
        *contract.metadata.setup_inputs,
        *contract.metadata.outputs,
    ):
        if field.repeated or field.maximum != 1:
            raise ValueError(
                "cloud procedures currently support only single-value fields"
            )
    record, resolved_configuration = resolve_record(
        definition, dict(configuration or {})
    )
    task = TaskPlan(
        procedure=procedure,
        record=record.to_dict(),
        configuration=resolved_configuration,
        inputs=dict(bindings["inputs"]),
        setup_inputs=dict(bindings["setup_inputs"]),
        value_types={
            f"{scope}.{field.name}": field.value_type
            for scope in bindings
            for field in getattr(contract.metadata, scope)
            if field.value_type is not None
        },
        output_types={
            field.name: cast(str, field.artifact_identifier)
            for field in contract.metadata.outputs
        },
        settings=deployment.resolve(
            procedure=procedure, node="procedure", tags=tags, overrides=overrides
        ),
        force=force,
    )
    return InvocationPlan(graph=graph, tasks={"procedure": task}, targets=graph.targets)


def resolve_record(
    definition: Any, configuration: Mapping[str, Any]
) -> tuple[ProcedureRecord, dict[str, Any]]:
    contract = definition.resolve_contract()
    if contract.configuration is None:
        if configuration:
            raise ValueError("procedure has no configuration")
        return ProcedureRecord(definition.identifier, contract.metadata.digest), {}
    config = contract.configuration.model_validate(configuration)
    snapshot = ConfigurationSnapshot.from_configuration(config)
    return ProcedureRecord(
        definition.identifier, contract.metadata.digest, snapshot.value, "pydantic-v2"
    ), config.model_dump(mode="json")
