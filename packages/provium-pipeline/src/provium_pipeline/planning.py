"""Contract validation without loading procedure implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from provium.procedure.config import ConfigurationSnapshot
from provium.procedure.discovery import discover_procedure_catalogs
from provium.provenance import ProcedureRecord

from .models import (
    ComputeProfile,
    PipelineConfiguration,
    PipelineDefinition,
    Settings,
    refs,
)


@dataclass
class Plan:
    configuration: PipelineConfiguration
    definition: PipelineDefinition | None
    procedures: dict[str, Any]
    records: dict[str, ProcedureRecord]
    configurations: dict[str, dict[str, Any]]
    output_types: dict[str, dict[str, str]]
    order: list[str]
    input_types: dict[str, str]


def build_plan(
    configuration: PipelineConfiguration,
    definition: PipelineDefinition | None = None,
    procedures: dict[str, Any] | None = None,
) -> Plan:
    configuration = PipelineConfiguration.model_validate_json(
        configuration.model_dump_json()
    )
    if definition is not None:
        definition = PipelineDefinition.from_snapshot(definition.snapshot())
    catalog: Any = discover_procedure_catalogs() if procedures is None else None
    resolved: dict[str, Any] = {}
    records: dict[str, ProcedureRecord] = {}
    configs: dict[str, dict[str, Any]] = {}
    output_types: dict[str, dict[str, str]] = {}
    order = configuration.order()
    types = {f"$inputs.{name}": value for name, value in configuration.inputs.items()}
    if definition is not None:
        if configuration.inputs.keys() != definition.inputs.keys():
            raise ValueError("pipeline input names do not match definition")
        for name, expected in definition.inputs.items():
            expected_type = (
                expected if isinstance(expected, str) else expected.identifier
            )
            if configuration.inputs[name] != expected_type:
                raise ValueError(f"incompatible pipeline input: {name}")
    for name in order:
        node = configuration.nodes[name]
        procedure: Any = (
            procedures[node.procedure]
            if procedures is not None
            else catalog.resolve(node.procedure)
        )
        resolved[name] = procedure
        contract: Any = procedure.resolve_contract()
        config_type = contract.configuration
        if config_type is None:
            if node.configuration:
                raise ValueError(f"{name}: procedure has no configuration")
            configs[name] = {}
            records[name] = ProcedureRecord(
                procedure.identifier, contract.metadata.digest
            )
        else:
            config = config_type.model_validate(node.configuration)
            snapshot = ConfigurationSnapshot.from_configuration(config)
            configs[name] = config.model_dump(mode="json")
            records[name] = ProcedureRecord(
                procedure.identifier,
                contract.metadata.digest,
                snapshot.value,
                "pydantic-v2",
            )
        for field_name, bindings in (
            ("inputs", node.inputs),
            ("setup_inputs", node.setup_inputs),
        ):
            fields = {
                item.name: item for item in getattr(contract.metadata, field_name)
            }
            if bindings.keys() - fields.keys():
                raise ValueError(f"{name}: unknown {field_name}")
            for key, field in fields.items():
                value = bindings.get(key)
                sources = refs(value)
                if field.repeated and value is not None and not isinstance(value, list):
                    raise ValueError(f"{name}.{key}: expected collection")
                if not field.repeated and isinstance(value, list):
                    raise ValueError(f"{name}.{key}: expected single artifact")
                if len(sources) < field.minimum or (
                    field.maximum is not None and len(sources) > field.maximum
                ):
                    raise ValueError(f"{name}.{key}: invalid binding cardinality")
                for source in sources:
                    if types.get(source) != field.artifact_identifier:
                        raise ValueError(
                            f"{name}.{key}: incompatible or unknown source {source}"
                        )
        output_types[name] = {
            item.name: item.artifact_identifier for item in contract.metadata.outputs
        }
        types.update(
            {f"$nodes.{name}.{key}": value for key, value in output_types[name].items()}
        )
    if definition is not None:
        if configuration.outputs.keys() != definition.outputs.keys():
            raise ValueError("pipeline output names do not match definition")
        for name, expected in definition.outputs.items():
            expected_type = (
                expected if isinstance(expected, str) else expected.identifier
            )
            if types.get(configuration.outputs[name]) != expected_type:
                raise ValueError(f"incompatible pipeline output: {name}")
    else:
        for source in configuration.outputs.values():
            if source not in types:
                raise ValueError(f"unknown pipeline output source: {source}")
    return Plan(
        configuration,
        definition,
        resolved,
        records,
        configs,
        output_types,
        order,
        {ref[8:]: kind for ref, kind in types.items() if ref.startswith("$inputs.")},
    )


def matches(graph: PipelineConfiguration, node: str, selectors: list[str]) -> bool:
    tags = graph.nodes[node].tags
    return any(
        selector == "all"
        or selector == f"nodes.{node}"
        or selector.startswith("tags.")
        and selector[5:] in tags
        for selector in selectors
    )


def resolve_settings(
    graph: PipelineConfiguration,
    profile: ComputeProfile,
    *,
    only: list[str],
    force: list[str],
    overrides: list[dict[str, Any]],
) -> dict[str, Settings]:
    for selector in only + force:
        if selector == "all" and selector in force:
            continue
        if not selector.startswith(("nodes.", "tags.")) or not any(
            matches(graph, node, [selector]) for node in graph.nodes
        ):
            raise ValueError(f"unknown or unmatched selector: {selector}")
    rules: list[dict[str, Any]] = []
    for rule in overrides:
        if len(rule) != 1:
            raise ValueError("worker override needs one selector")
        selector, value = next(iter(rule.items()))
        if selector.startswith("nodes."):
            if selector[6:] not in graph.nodes:
                raise ValueError(f"unknown node: {selector}")
            selector = f"pipelines.{graph.name}.{selector}"
        rules.append({selector: value})
    ComputeProfile(name="overrides", workers=rules)
    for rule in (
        profile.workers + profile.soft_time_limits + profile.hard_time_limits + rules
    ):
        selector = next(iter(rule))
        prefix = f"pipelines.{graph.name}."
        applicable = (
            selector[len(prefix) :] if selector.startswith(prefix) else selector
        )
        if applicable.startswith(("tags.", "nodes.")) and not any(
            matches(graph, n, [applicable]) for n in graph.nodes
        ):
            raise ValueError(f"unknown or unmatched selector: {selector}")
    return {
        name: profile.settings(graph.name, name, node.tags, rules)
        for name, node in graph.nodes.items()
    }
