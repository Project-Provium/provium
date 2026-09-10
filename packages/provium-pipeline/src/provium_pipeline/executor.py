"""Execute a validated pipeline with isolated, ephemeral intermediate storage."""

from collections.abc import Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from provium import (
    ArtifactReadBinding,
    ArtifactReference,
    ArtifactWriteBinding,
    CancellationToken,
    ProcedureDefinition,
    ProcedureExecutionResult,
    ProcedureExecutor,
    discover_procedure_catalogs,
)

from .models import Pipeline
from .planning import PipelinePlan, Reference, compile_pipeline


@dataclass(frozen=True)
class PipelineExecutionResult:
    """Completed step results and public artifact references from one run."""

    identity: str
    outputs: Mapping[str, ArtifactReference]
    steps: Mapping[str, ProcedureExecutionResult]
    intermediate_directory: Path | None


class PipelineExecutor:
    """Plan and execute pipelines using the ordinary Provium procedure executor."""

    def __init__(
        self,
        *,
        procedures: Mapping[str, ProcedureDefinition[Any]] | None = None,
    ) -> None:
        self._procedures = procedures

    def plan(
        self,
        pipeline: Pipeline,
        *,
        configuration_layers: Iterable[Mapping[str, object]] = (),
    ) -> PipelinePlan:
        """Validate configuration, ports, types and dependencies without running."""
        procedures = self._procedures
        if procedures is None:
            procedures = discover_procedure_catalogs().definitions
        return compile_pipeline(pipeline, procedures, configuration_layers)

    def execute(
        self,
        pipeline: Pipeline,
        *,
        configuration_layers: Iterable[Mapping[str, object]] = (),
        inputs: Mapping[str, ArtifactReadBinding[Any]] | None = None,
        outputs: Mapping[str, ArtifactWriteBinding[Any]] | None = None,
        cancellation: CancellationToken | None = None,
        keep_intermediates: str | PathLike[str] | None = None,
    ) -> PipelineExecutionResult:
        """Run once; clean temporary artifacts even when a step fails or cancels.

        Public outputs are committed by their producing step, not atomically
        across the whole pipeline. Retention creates a unique run subdirectory.
        """
        plan = self.plan(pipeline, configuration_layers=configuration_layers)
        supplied_inputs = dict(inputs or {})
        supplied_outputs = dict(outputs or {})
        self._validate_bindings(plan, supplied_inputs, supplied_outputs)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        with ExitStack() as stack:
            retained: Path | None = None
            if keep_intermediates is None:
                directory = Path(
                    stack.enter_context(TemporaryDirectory(prefix="provium-pipeline-"))
                )
            else:
                root = Path(keep_intermediates)
                root.mkdir(parents=True, exist_ok=True)
                directory = retained = Path(mkdtemp(prefix="run-", dir=root))
            return self._run(
                plan,
                supplied_inputs,
                supplied_outputs,
                directory,
                retained,
                cancellation,
            )

    @staticmethod
    def _validate_bindings(
        plan: PipelinePlan,
        inputs: Mapping[str, ArtifactReadBinding[Any]],
        outputs: Mapping[str, ArtifactWriteBinding[Any]],
    ) -> None:
        if inputs.keys() != plan.inputs.keys():
            raise ValueError(f"pipeline requires input bindings: {sorted(plan.inputs)}")
        if outputs.keys() != plan.outputs.keys():
            raise ValueError(
                f"pipeline requires output bindings: {sorted(plan.outputs)}"
            )
        steps = {step.name: step for step in plan.steps}
        for name, binding in inputs.items():
            if not isinstance(binding, ArtifactReadBinding):
                raise TypeError(f"input {name!r} requires an ArtifactReadBinding")
            if binding.artifact is not plan.inputs[name].resolve():
                raise TypeError(f"input {name!r} has an incompatible artifact type")
            if not binding.path.is_file():
                raise ValueError(f"input {name!r} does not exist: {binding.path}")
        destinations: set[Path] = set()
        sources = {binding.path.resolve() for binding in inputs.values()}
        for name, binding in outputs.items():
            if not isinstance(binding, ArtifactWriteBinding):
                raise TypeError(f"output {name!r} requires an ArtifactWriteBinding")
            reference = plan.outputs[name]
            assert reference.step is not None
            artifact = steps[reference.step].outputs[reference.name].resolve()
            if binding.artifact is not artifact:
                raise TypeError(f"output {name!r} has an incompatible artifact type")
            path = binding.path.resolve()
            if path in destinations or path in sources:
                raise ValueError(f"output destination overlaps another binding: {path}")
            if path.is_dir():
                raise ValueError(f"output destination is a directory: {path}")
            destinations.add(path)

    @staticmethod
    def _run(
        plan: PipelinePlan,
        inputs: Mapping[str, ArtifactReadBinding[Any]],
        outputs: Mapping[str, ArtifactWriteBinding[Any]],
        directory: Path,
        retained: Path | None,
        cancellation: CancellationToken | None,
    ) -> PipelineExecutionResult:
        reads = {Reference(name): binding for name, binding in inputs.items()}
        public = {reference: outputs[name] for name, reference in plan.outputs.items()}
        results: dict[str, ProcedureExecutionResult] = {}
        executor = ProcedureExecutor()

        def bind_inputs(
            connections: Mapping[str, Reference | tuple[Reference, ...]],
        ) -> dict[str, ArtifactReadBinding[Any] | list[ArtifactReadBinding[Any]]]:
            return {
                name: [reads[item] for item in reference]
                if isinstance(reference, tuple)
                else reads[reference]
                for name, reference in connections.items()
            }

        for index, step in enumerate(plan.steps):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            step_outputs: dict[str, ArtifactWriteBinding[Any]] = {}
            for port, definition in step.outputs.items():
                reference = Reference(port, step.name)
                binding = public.get(reference)
                if binding is None:
                    binding = definition.resolve().bind_write(
                        directory / f"{index}-{step.name}-{port}.pa"
                    )
                step_outputs[port] = binding
            try:
                result = executor.execute(
                    step.procedure,
                    configuration_layers=[step.configuration],
                    setup_inputs=bind_inputs(step.setup_inputs),
                    inputs=bind_inputs(step.inputs),
                    outputs=step_outputs,
                    cancellation=cancellation,
                )
            except Exception as error:
                error.add_note(f"pipeline {plan.name!r}, step {step.name!r}")
                raise
            results[step.name] = result
            for port, binding in step_outputs.items():
                reads[Reference(port, step.name)] = binding.artifact.bind_read(
                    binding.path
                )
        references: dict[str, ArtifactReference] = {}
        for name, reference in plan.outputs.items():
            assert reference.step is not None
            references[name] = results[reference.step].outputs[reference.name]
        return PipelineExecutionResult(
            str(uuid4()),
            MappingProxyType(references),
            MappingProxyType(results),
            retained,
        )
