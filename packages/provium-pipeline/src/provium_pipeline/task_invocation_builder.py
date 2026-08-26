"""Reconstruct prepared procedure invocations from frozen task state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, cast

from provium_pipeline.artifact import ArtifactLocation, ManagedArtifactDescriptor
from provium_pipeline.attempts import TaskAttemptLease
from provium_pipeline.canonical import canonical_digest
from provium_pipeline.compiler.models import CompiledBindingPlan
from provium_pipeline.identifiers import InputRecordKey, RunId
from provium_pipeline.inputs import RunInputSnapshot
from provium_pipeline.local_task_attempt import BuiltTaskInvocation
from provium_pipeline.run_models import PipelineRun, PipelineTask
from provium_pipeline.task_executor import (
    AttemptMaterializations,
    PreparedInvocation,
    PreparedProcedureKey,
    StoredArtifact,
    build_output_bindings,
    build_read_binding_value,
    materialize_binding_inputs,
    resolve_runtime_binding_identities,
    verify_configuration_snapshot,
    verify_procedure_contract,
)


class TaskInputRecordNotFoundError(LookupError):
    """A frozen task refers to a record absent from its run snapshot."""


class RunSnapshotLookup(Protocol):
    def get_run(self, identifier: RunId) -> PipelineRun: ...


class ArtifactLookup(Protocol):
    def get_artifact(
        self,
        artifact_identity: str,
    ) -> ManagedArtifactDescriptor: ...

    def get_active_locations(
        self,
        artifact_identity: str,
    ) -> tuple[ArtifactLocation, ...]: ...


class UpstreamOutputLookup(Protocol):
    def resolve(
        self,
        run_identifier: RunId,
        record_key: InputRecordKey,
        reference: str,
    ) -> Sequence[str]: ...


class FrozenTaskInvocationBuilder:
    """Build one exact prepared invocation from durable run and task snapshots."""

    def __init__(
        self,
        *,
        runs: Any,
        procedures: Any,
        artifact_catalogs: Any,
        artifact_index: ArtifactLookup,
        artifact_store: Any,
        outputs: UpstreamOutputLookup,
        workspace: Path,
    ) -> None:
        self._runs = runs
        self._procedures = procedures
        self._artifact_catalogs = artifact_catalogs
        self._artifact_index = artifact_index
        self._artifact_store = artifact_store
        self._outputs = outputs
        self._workspace = workspace

    def build(
        self,
        task: PipelineTask,
        lease: TaskAttemptLease,
    ) -> BuiltTaskInvocation:
        run = self._runs.get_run(task.run_identifier)
        record = self._record(run.inputs, task)
        definition = self._procedures.resolve(task.procedure_identifier)
        contract = cast(
            Any,
            verify_procedure_contract(
                definition,
                task.procedure_contract_digest,
            ),
        )
        verify_configuration_snapshot(
            task.configuration_snapshot,
            contract.configuration,
        )
        ownership = AttemptMaterializations()
        try:
            return self._build_invocation(
                task,
                lease,
                run,
                record,
                definition,
                contract,
                ownership,
            )
        except BaseException as error:
            try:
                ownership.close()
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise

    def _build_invocation(
        self,
        task: PipelineTask,
        lease: TaskAttemptLease,
        run: Any,
        record: Any,
        definition: Any,
        contract: Any,
        ownership: AttemptMaterializations,
    ) -> BuiltTaskInvocation:
        attempt_workspace = (
            self._workspace
            / str(task.identifier)
            / f"attempt-{lease.attempt_number:04d}"
        )
        stored: dict[str, StoredArtifact] = {}
        resolved_by_plan: dict[CompiledBindingPlan, tuple[str, ...]] = {}

        def identities(plan: CompiledBindingPlan) -> tuple[str, ...]:
            resolved = resolve_runtime_binding_identities(
                plan,
                record=record,
                shared_inputs=run.inputs.shared_inputs,
                resolve_upstream=lambda reference: self._outputs.resolve(
                    task.run_identifier,
                    task.record_key,
                    reference,
                ),
            )
            resolved_by_plan[plan] = resolved
            for identity in resolved:
                if identity not in stored:
                    stored[identity] = StoredArtifact(
                        descriptor=self._artifact_index.get_artifact(identity),
                        locations=self._artifact_index.get_active_locations(identity),
                    )
            return resolved

        setup_inputs = self._binding_values(
            task.setup_bindings,
            identities,
            stored,
            attempt_workspace / "setup",
            ownership,
        )
        inputs = self._binding_values(
            task.input_bindings,
            identities,
            stored,
            attempt_workspace / "inputs",
            ownership,
        )
        output_bindings = build_output_bindings(
            task.expected_output_fields,
            contract.metadata,
            self._artifact_catalogs,
            attempt_workspace / "outputs",
        )
        setup_fingerprint = canonical_digest(
            [
                identity
                for plan in task.setup_bindings
                for identity in resolved_by_plan[plan]
            ]
        )
        snapshot = task.configuration_snapshot
        return BuiltTaskInvocation(
            request=PreparedInvocation(
                key=PreparedProcedureKey(
                    procedure=(
                        f"{task.procedure_identifier}:{task.procedure_contract_digest}"
                    ),
                    configuration=(
                        snapshot.value_digest if snapshot is not None else "none"
                    ),
                    setup=setup_fingerprint,
                ),
                definition=definition,
                configuration_layers=(
                    ()
                    if snapshot is None
                    else (cast(Mapping[str, object], snapshot.value),)
                ),
                setup_inputs=setup_inputs or None,
                inputs=inputs or None,
                outputs=output_bindings or None,
                cancellation=None,
            ),
            materializations=ownership,
        )

    def _binding_values(
        self,
        plans: tuple[CompiledBindingPlan, ...],
        identities: Any,
        stored: Mapping[str, StoredArtifact],
        workspace: Path,
        ownership: AttemptMaterializations,
    ) -> dict[str, object]:
        values: dict[str, object] = {}
        for plan in plans:
            resolved = identities(plan)
            paths = materialize_binding_inputs(
                plan,
                resolved,
                stored,
                self._artifact_store,
                workspace,
                ownership,
            )
            values[plan.field] = build_read_binding_value(
                plan,
                paths,
                self._artifact_catalogs,
            )
        return values

    @staticmethod
    def _record(inputs: RunInputSnapshot, task: PipelineTask):
        for record in inputs.records:
            if record.key == task.record_key:
                return record
        raise TaskInputRecordNotFoundError(
            f"input record {task.record_key} is absent from run {task.run_identifier}"
        )


__all__ = ["FrozenTaskInvocationBuilder", "TaskInputRecordNotFoundError"]
