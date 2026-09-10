"""Validate connections and compile a dependency-ordered execution plan."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from types import MappingProxyType
from typing import Any, cast

from provium import (
    ArtifactDefinition,
    ProcedureConfig,
    ProcedureDefinition,
    ProcedureIOField,
    compose_configuration,
)

from .models import Pipeline


@dataclass(frozen=True)
class Reference:
    """An external input or a named output port of a step."""

    name: str
    step: str | None = None

    @classmethod
    def parse(cls, value: str) -> "Reference":
        parts = value.split(".")
        if len(parts) == 2 and parts[0] == "$inputs" and parts[1]:
            return cls(parts[1])
        if (
            len(parts) == 4
            and parts[0] == "$steps"
            and parts[2] == "outputs"
            and parts[1]
            and parts[3]
        ):
            return cls(parts[3], parts[1])
        raise ValueError(f"invalid artifact reference: {value!r}")


@dataclass(frozen=True)
class PlannedStep:
    """A validated invocation, without paths or execution state."""

    name: str
    procedure: ProcedureDefinition[Any]
    configuration: Mapping[str, object]
    setup_inputs: Mapping[str, Reference | tuple[Reference, ...]]
    inputs: Mapping[str, Reference | tuple[Reference, ...]]
    outputs: Mapping[str, ArtifactDefinition[Any]]


@dataclass(frozen=True)
class PipelinePlan:
    """Storage-independent plan; executions allocate their own artifacts."""

    name: str
    steps: tuple[PlannedStep, ...]
    inputs: Mapping[str, ArtifactDefinition[Any]]
    outputs: Mapping[str, Reference]


def _lookup(value: object, key: str, reference: str) -> object:
    if not isinstance(value, Mapping) or key not in value:
        raise ValueError(f"unknown configuration reference: {reference}")
    return cast(Mapping[str, object], value)[key]


def _configuration(value: object, config: Mapping[str, object]) -> object:
    if isinstance(value, str):
        if value.startswith("$$"):
            return value[1:]
        if value.startswith("$config."):
            current: object = config
            for key in value[len("$config.") :].split("."):
                current = _lookup(current, key, value)
            return current
        if value.startswith("$"):
            raise ValueError(f"invalid configuration reference: {value}")
    if isinstance(value, dict):
        return {
            key: _configuration(item, config)
            for key, item in cast(dict[str, object], value).items()
        }
    if isinstance(value, list):
        return [_configuration(item, config) for item in cast(list[object], value)]
    return value


def compile_pipeline(
    pipeline: Pipeline,
    procedures: Mapping[str, ProcedureDefinition[Any]],
    layers: Iterable[Mapping[str, object]],
) -> PipelinePlan:
    """Check all contracts and configuration before any procedure is started."""
    if len(set(pipeline.inputs)) != len(pipeline.inputs):
        raise ValueError("duplicate pipeline input names")
    config = compose_configuration((pipeline.configuration, *layers))
    definitions: dict[str, ProcedureDefinition[Any]] = {}
    output_fields: dict[str, Mapping[str, ProcedureIOField]] = {}
    for name, step in pipeline.steps.items():
        if step.procedure not in procedures:
            raise ValueError(f"step {name}: unknown procedure {step.procedure!r}")
        definition = procedures[step.procedure]
        definitions[name] = definition
        contract = definition.resolve_contract()
        output_fields[name] = contract.Outputs.fields if contract.Outputs else {}

    inferred: dict[str, ArtifactDefinition[Any]] = {}
    dependencies: dict[str, set[str]] = {name: set() for name in pipeline.steps}

    def source_artifact(reference: Reference) -> ArtifactDefinition[Any]:
        if reference.step not in output_fields:
            raise ValueError(f"unknown step: {reference.step!r}")
        fields = output_fields[reference.step]
        if reference.name not in fields:
            raise ValueError(
                f"unknown output {reference.name!r} of step {reference.step!r}"
            )
        return fields[reference.name].artifact

    def connections(
        name: str,
        supplied: Mapping[str, str | list[str]],
        fields: Mapping[str, ProcedureIOField],
    ) -> Mapping[str, Reference | tuple[Reference, ...]]:
        unknown = supplied.keys() - fields.keys()
        if unknown:
            raise ValueError(f"step {name}: unknown input ports {sorted(unknown)}")
        result: dict[str, Reference | tuple[Reference, ...]] = {}
        for port, field in fields.items():
            if port not in supplied:
                if field.required:
                    raise ValueError(f"step {name}: missing input {port!r}")
                continue
            value = supplied[port]
            if isinstance(value, list) != field.repeated:
                raise ValueError(f"step {name}: input {port!r} cardinality mismatch")
            references = tuple(
                Reference.parse(item)
                for item in (value if isinstance(value, list) else [value])
            )
            if len(references) < field.minimum or (
                field.maximum is not None and len(references) > field.maximum
            ):
                raise ValueError(f"step {name}: input {port!r} cardinality mismatch")
            for reference in references:
                if reference.step is None:
                    if reference.name not in pipeline.inputs:
                        raise ValueError(f"unknown pipeline input: {reference.name!r}")
                    artifact = inferred.setdefault(reference.name, field.artifact)
                else:
                    artifact = source_artifact(reference)
                    dependencies[name].add(reference.step)
                if artifact.identifier != field.artifact.identifier:
                    raise ValueError(f"step {name}: incompatible artifact for {port!r}")
            result[port] = references if field.repeated else references[0]
        return MappingProxyType(result)

    public_outputs: dict[str, Reference] = {}
    for name, value in pipeline.outputs.items():
        reference = Reference.parse(value)
        source_artifact(reference)
        if reference in public_outputs.values():
            raise ValueError("a step output may only be exposed once")
        public_outputs[name] = reference

    planned: dict[str, PlannedStep] = {}
    for name, step in pipeline.steps.items():
        definition = definitions[name]
        contract = definition.resolve_contract()
        values = cast(dict[str, object], _configuration(step.configuration, config))
        configuration_type = cast(
            type[ProcedureConfig] | None, getattr(contract, "configuration")
        )
        if configuration_type is None:
            if values:
                raise ValueError(
                    f"step {name}: procedure does not accept configuration"
                )
        else:
            configuration_type.model_validate(values)
        setup = connections(
            name,
            step.setup_inputs,
            contract.SetupInputs.fields if contract.SetupInputs else {},
        )
        inputs = connections(
            name,
            step.inputs,
            contract.Inputs.fields if contract.Inputs else {},
        )
        planned[name] = PlannedStep(
            name,
            definition,
            MappingProxyType(values),
            setup,
            inputs,
            MappingProxyType(
                {port: field.artifact for port, field in output_fields[name].items()}
            ),
        )
    unused = set(pipeline.inputs) - inferred.keys()
    if unused:
        raise ValueError(f"unused pipeline inputs cannot be typed: {sorted(unused)}")
    try:
        order = tuple(TopologicalSorter(dependencies).static_order())
    except CycleError as error:
        raise ValueError("pipeline contains a dependency cycle") from error
    return PipelinePlan(
        pipeline.name,
        tuple(planned[name] for name in order),
        MappingProxyType(inferred),
        MappingProxyType(public_outputs),
    )
