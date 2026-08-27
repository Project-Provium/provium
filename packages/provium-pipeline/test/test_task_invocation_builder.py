from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import provium_pipeline.task_invocation_builder as builder_module
from provium_pipeline.artifact import (
    ArtifactLocation,
    ManagedArtifactDescriptor,
    MaterializationCleanup,
    MaterializedArtifact,
)
from provium_pipeline.attempts import TaskAttemptLease
from provium_pipeline.compiler.models import CompiledBindingPlan
from provium_pipeline.identifiers import InputRecordKey, RunId, TaskId
from provium_pipeline.input.models import InputRecord, RunInputSnapshot
from provium_pipeline.run_models import PipelineTask, TaskState
from provium_pipeline.task_executor import AttemptMaterializations, StoredArtifact
from provium_pipeline.task_invocation_builder import (
    FrozenTaskInvocationBuilder,
    TaskInputRecordNotFoundError,
)
from provium_pipeline.task_outputs import InMemoryTaskOutputStore, TaskOutputSet

NOW = datetime(2026, 8, 26, tzinfo=UTC)


class _Runs:
    def __init__(self, inputs: RunInputSnapshot) -> None:
        self.inputs = inputs

    def get_run(self, identifier: RunId) -> object:
        del identifier
        return SimpleNamespace(inputs=self.inputs)


class _Definition:
    def __init__(self, contract: object) -> None:
        self.contract = contract

    def resolve_contract(self) -> object:
        return self.contract


class _Procedures:
    def __init__(self, definition: object) -> None:
        self.definition = definition
        self.identifiers: list[str] = []

    def resolve(self, identifier: str) -> Any:
        self.identifiers.append(identifier)
        return self.definition


class _Index:
    def __init__(self) -> None:
        self.identities: list[str] = []

    def get_artifact(self, artifact_identity: str) -> ManagedArtifactDescriptor:
        self.identities.append(artifact_identity)
        return cast(ManagedArtifactDescriptor, f"descriptor:{artifact_identity}")

    def get_active_locations(
        self,
        artifact_identity: str,
    ) -> tuple[ArtifactLocation, ...]:
        return (cast(ArtifactLocation, f"location:{artifact_identity}"),)


@dataclass(frozen=True)
class _Metadata:
    digest: str


@dataclass(frozen=True)
class _Contract:
    metadata: _Metadata
    configuration: object | None = None


def _task(*, record_key: str = "record-1") -> PipelineTask:
    setup = CompiledBindingPlan(
        field="model",
        artifact_identifier="example/model@1",
        minimum=1,
        maximum=1,
        references=("$inputs.model",),
    )
    inputs = CompiledBindingPlan(
        field="images",
        artifact_identifier="example/image@1",
        minimum=1,
        maximum=None,
        references=("$inputs.images", "$nodes.decode.preview"),
    )
    return PipelineTask(
        identifier=TaskId.new(),
        run_identifier=RunId.new(),
        node_identifier="detect",
        record_key=InputRecordKey(record_key),
        procedure_identifier="example/detect@1",
        procedure_contract_digest="contract-digest",
        configuration_snapshot=None,
        setup_bindings=(setup,),
        input_bindings=(inputs,),
        expected_output_fields=("detections",),
        dependencies=(),
        state=TaskState.LEASED,
    )


def _lease(task: PipelineTask) -> TaskAttemptLease:
    return TaskAttemptLease(
        task_identifier=task.identifier,
        attempt_number=2,
        worker_identity="serial-1",
        token="token",
        started_at=NOW,
        heartbeat_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )


def test_frozen_builder_reconstructs_ordered_attempt_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task()
    record = InputRecord(
        key=task.record_key,
        inputs={"images": ("image-1", "image-1", "image-2")},
        labels={},
    )
    snapshot = RunInputSnapshot.create(
        records=(
            InputRecord(key=InputRecordKey("record-0"), inputs={}, labels={}),
            record,
        ),
        shared_inputs={"model": ("model-1",)},
        sources=(),
    )
    contract = _Contract(_Metadata("contract-digest"))
    procedures = _Procedures(_Definition(contract))
    index = _Index()
    outputs = InMemoryTaskOutputStore()
    upstream_task = replace(
        task,
        identifier=TaskId.new(),
        node_identifier="decode",
        expected_output_fields=("preview",),
    )
    outputs.record(
        TaskOutputSet(
            task_identifier=upstream_task.identifier,
            run_identifier=task.run_identifier,
            record_key=task.record_key,
            node_identifier="decode",
            outputs={"preview": "preview-1"},
        )
    )
    observed: list[tuple[str, tuple[str, ...], Path]] = []

    def materialize(
        plan: CompiledBindingPlan,
        identities: tuple[str, ...],
        _artifacts: Mapping[str, StoredArtifact],
        _store: object,
        workspace: Path,
        _ownership: AttemptMaterializations,
    ) -> tuple[Path, ...]:
        observed.append((plan.field, identities, workspace))
        return tuple(workspace / f"{identity}.pa" for identity in identities)

    def read_value(
        plan: CompiledBindingPlan,
        paths: Sequence[Path],
        _catalogs: object,
    ) -> object:
        return (plan.field, tuple(paths))

    def output_values(
        fields: Sequence[str],
        _metadata: object,
        _catalogs: object,
        workspace: Path,
    ) -> dict[str, object]:
        return {field: workspace / f"{field}.pa" for field in fields}

    monkeypatch.setattr(builder_module, "materialize_binding_inputs", materialize)
    monkeypatch.setattr(builder_module, "build_read_binding_value", read_value)
    monkeypatch.setattr(builder_module, "build_output_bindings", output_values)

    built = FrozenTaskInvocationBuilder(
        runs=_Runs(snapshot),
        procedures=procedures,
        artifact_catalogs=object(),
        artifact_index=index,
        artifact_store=object(),
        outputs=outputs,
        workspace=tmp_path,
    ).build(task, _lease(task))

    assert procedures.identifiers == [task.procedure_identifier]
    assert index.identities == ["model-1", "image-1", "image-2", "preview-1"]
    assert [item[:2] for item in observed] == [
        ("model", ("model-1",)),
        ("images", ("image-1", "image-1", "image-2", "preview-1")),
    ]
    assert built.request.definition is procedures.definition
    assert built.request.setup_inputs["model"][0] == "model"
    assert built.request.inputs["images"][0] == "images"
    assert tuple(built.request.outputs) == ("detections",)
    assert built.request.key.procedure.startswith(task.procedure_identifier)
    assert built.request.key.setup
    assert built.request.cancellation is None


def test_frozen_builder_cleans_owned_materializations_after_build_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = replace(_task(), input_bindings=())
    record = InputRecord(key=task.record_key, inputs={}, labels={})
    snapshot = RunInputSnapshot.create(
        records=(record,),
        shared_inputs={"model": ("model-1",)},
        sources=(),
    )
    owned = tmp_path / "owned.pa"

    def materialize(
        _plan: CompiledBindingPlan,
        _identities: tuple[str, ...],
        artifacts: Mapping[str, StoredArtifact],
        _store: object,
        _workspace: Path,
        ownership: AttemptMaterializations,
    ) -> tuple[Path, ...]:
        owned.write_text("materialized", encoding="utf-8")
        descriptor = next(iter(artifacts.values())).descriptor
        ownership.track(
            MaterializedArtifact(
                descriptor=descriptor,
                path=owned,
                cleanup=MaterializationCleanup.REQUIRED,
            )
        )
        return (owned,)

    def fail_outputs(*_args: object) -> dict[str, object]:
        raise RuntimeError("output construction failed")

    def read_value(
        _plan: CompiledBindingPlan,
        paths: Sequence[Path],
        _catalogs: object,
    ) -> object:
        return tuple(paths)

    monkeypatch.setattr(builder_module, "materialize_binding_inputs", materialize)
    monkeypatch.setattr(builder_module, "build_read_binding_value", read_value)
    monkeypatch.setattr(builder_module, "build_output_bindings", fail_outputs)

    with pytest.raises(RuntimeError, match="output construction failed"):
        FrozenTaskInvocationBuilder(
            runs=_Runs(snapshot),
            procedures=_Procedures(
                _Definition(_Contract(_Metadata("contract-digest")))
            ),
            artifact_catalogs=object(),
            artifact_index=_Index(),
            artifact_store=object(),
            outputs=InMemoryTaskOutputStore(),
            workspace=tmp_path,
        ).build(task, _lease(task))

    assert not owned.exists()


def test_frozen_builder_preserves_build_error_over_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = replace(_task(), setup_bindings=(), input_bindings=())
    record = InputRecord(key=task.record_key, inputs={}, labels={})
    snapshot = RunInputSnapshot.create(records=(record,), shared_inputs={}, sources=())

    class FailingOwnership:
        def close(self) -> None:
            raise OSError("cleanup failed")

    def fail_outputs(*_args: object) -> dict[str, object]:
        raise RuntimeError("build failed")

    monkeypatch.setattr(builder_module, "AttemptMaterializations", FailingOwnership)
    monkeypatch.setattr(builder_module, "build_output_bindings", fail_outputs)

    with pytest.raises(RuntimeError, match="build failed") as raised:
        FrozenTaskInvocationBuilder(
            runs=_Runs(snapshot),
            procedures=_Procedures(
                _Definition(_Contract(_Metadata("contract-digest")))
            ),
            artifact_catalogs=object(),
            artifact_index=_Index(),
            artifact_store=object(),
            outputs=InMemoryTaskOutputStore(),
            workspace=tmp_path,
        ).build(task, _lease(task))

    assert isinstance(raised.value.__cause__, OSError)


def test_frozen_builder_rejects_missing_record(tmp_path: Path) -> None:
    task = _task(record_key="missing")
    snapshot = RunInputSnapshot.create(records=(), shared_inputs={}, sources=())

    with pytest.raises(TaskInputRecordNotFoundError, match="missing"):
        FrozenTaskInvocationBuilder(
            runs=_Runs(snapshot),
            procedures=_Procedures(_Definition(_Contract(_Metadata("digest")))),
            artifact_catalogs=object(),
            artifact_index=_Index(),
            artifact_store=object(),
            outputs=InMemoryTaskOutputStore(),
            workspace=tmp_path,
        ).build(task, _lease(task))
