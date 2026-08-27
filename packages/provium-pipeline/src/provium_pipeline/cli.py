"""Core-CLI plugin exposing pipeline command families."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from time import sleep
from typing import Any, ClassVar, Protocol, cast

import yaml

from provium import JsonValue
from provium.artifact.discovery import discover_artifact_catalogs
from provium.canonical import canonical_json
from provium.cli.catalog import CommandCatalog
from provium.cli.command import Command
from provium.cli.plugin import CLI_PLUGIN_API_VERSION, CLIPlugin
from provium.procedure.discovery import discover_procedure_catalogs

from .artifact.query import (
    ArtifactQuery,
    ArtifactQueryService,
    InputSetArtifactBinding,
    RunArtifactBinding,
)
from .compiler.catalogs import ArtifactCatalogCollection, ProcedureCatalogCollection
from .compiler.compiler import PipelineCompiler
from .compiler.diagnostics import PipelineCompilationError
from .compiler.validation import validate_pipeline_definition
from .definition.codec import (
    canonical_definition_document,
    load_pipeline_json,
    load_pipeline_yaml,
)
from .definition.models import PipelineDefinition
from .discovery import discover_pipeline_catalogs
from .dispatch_codec import dispatch_document
from .dispatch_models import DependencyPolicy, Dispatch, TaskSelection
from .dispatch_store import DispatchStore
from .execution_codec import pipeline_run_document, to_json_value
from .exports import RunExportService
from .exports import RunLookup as ExportRunLookup
from .identifiers import DispatchId, InputSetIdentifier, RunId
from .input_codec import load_input_records_ndjson
from .inputs import InputRecord, InputSet
from .run_creation import RunCreationService
from .run_models import PipelineRun
from .run_query import RunLookup, RunQueryService


@dataclass(frozen=True, slots=True)
class CLIResult:
    data: Mapping[str, object]
    exit_code: int = 0
    message: str | None = None


class PipelineCLIBackend(Protocol):
    def execute(
        self,
        group: str,
        action: str,
        arguments: argparse.Namespace,
    ) -> CLIResult: ...


class RunExecutor(Protocol):
    """Execute a selected subset of an existing run."""

    def execute(
        self,
        run_identifier: RunId,
        *,
        selection: TaskSelection,
        dependency_policy: DependencyPolicy,
    ) -> Dispatch: ...


class RunCanceller(Protocol):
    def cancel_run(self, identifier: RunId) -> PipelineRun: ...


class RunCreator(Protocol):
    def create(
        self,
        definition: PipelineDefinition,
        *,
        input_set: str,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> PipelineRun: ...


class ResolvedRunCreator(Protocol):
    def create(
        self,
        definition: PipelineDefinition,
        *,
        resolver_identifier: str,
        configuration: Mapping[str, JsonValue],
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> PipelineRun: ...


class InputSetStore(Protocol):
    def create(
        self,
        *,
        identifier: InputSetIdentifier,
        records: tuple[InputRecord, ...],
        metadata: Mapping[str, JsonValue],
    ) -> InputSet: ...

    def get(self, identity: str | InputSetIdentifier) -> InputSet: ...

    def list(self) -> tuple[InputSet, ...]: ...


class RunExporter(Protocol):
    def configuration_json(self, identifier: RunId) -> str: ...

    def run_bundle_json(self, identifier: RunId) -> str: ...


def _installed_validation_catalogs() -> tuple[
    ArtifactCatalogCollection,
    ProcedureCatalogCollection,
]:
    return (
        ArtifactCatalogCollection((discover_artifact_catalogs(),)),
        ProcedureCatalogCollection((discover_procedure_catalogs(),)),
    )


def _load_pipeline_source(source: str) -> PipelineDefinition:
    path = Path(source)
    if not path.exists():
        return discover_pipeline_catalogs().catalog.get(source)
    content = path.read_text()
    if path.suffix == ".json":
        return load_pipeline_json(content, source=str(path))
    if path.suffix in {".yaml", ".yml"}:
        return load_pipeline_yaml(content, source=str(path))
    raise ValueError(f"unsupported pipeline source extension: {path.suffix}")


def _no_locations(identity: str) -> tuple[object, ...]:
    return ()


def _labels(values: list[str]) -> dict[str, JsonValue]:
    labels: dict[str, JsonValue] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise ValueError(f"invalid label, expected KEY=VALUE: {value}")
        labels[key] = item
    return labels


def _selection_document(arguments: argparse.Namespace) -> dict[str, JsonValue]:
    return {
        "all": arguments.all,
        "all_remaining": arguments.all_remaining,
        "include_missing_upstream": arguments.include_missing_upstream,
        "labels": list(arguments.label),
        "nodes": list(arguments.node),
        "only_failed": arguments.only_failed,
        "procedures": list(arguments.procedure),
        "records": list(arguments.record),
    }


def _input_record_document(record: InputRecord) -> dict[str, JsonValue]:
    return {
        "inputs": {name: list(values) for name, values in record.inputs.items()},
        "key": str(record.key),
        "labels": dict(record.labels),
    }


def _input_set_summary(value: InputSet) -> dict[str, JsonValue]:
    return {
        "created_at": value.created_at.isoformat(),
        "digest": value.digest,
        "identifier": str(value.identifier),
        "identity": value.identity,
        "metadata": dict(value.metadata),
        "record_count": len(value.records),
    }


def _input_set_document(value: InputSet) -> dict[str, JsonValue]:
    return {
        "input_set": _input_set_summary(value),
        "records": [_input_record_document(record) for record in value.records],
    }


class LocalCLIBackend:
    """Read durable local run state through the execution-store contract."""

    def __init__(
        self,
        store: RunLookup,
        *,
        exporter: RunExporter | None = None,
        input_sets: InputSetStore | None = None,
        dispatches: DispatchStore | None = None,
        artifact_locations: Callable[[str], Iterable[object]] | None = None,
        run_creator: RunCreator | None = None,
        resolved_run_creator: ResolvedRunCreator | None = None,
        run_executor: RunExecutor | None = None,
        run_outputs: Callable[[object], Sequence[object]] | None = None,
        run_artifacts: Callable[[str, bool], Sequence[object]] | None = None,
    ) -> None:
        self._store = cast(ExportRunLookup, store)
        self._canceller = cast(RunCanceller, store)
        self._runs = RunQueryService(store, outputs=run_outputs)
        self._exporter = exporter or RunExportService(self._store)
        self._input_sets = input_sets
        self._dispatches = dispatches
        self._artifact_locations = artifact_locations or _no_locations
        self._run_creator = run_creator
        self._resolved_run_creator = resolved_run_creator
        self._run_executor = run_executor
        self._run_artifacts = run_artifacts

    def execute(
        self,
        group: str,
        action: str,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        if group == "input-set" and self._input_sets is not None:
            return self._input_set(action, arguments)
        if group == "dispatch" and self._dispatches is not None:
            return self._read_dispatch(action, arguments)
        if group == "pipeline" and action == "list":
            return self._list_pipelines()
        if group == "pipeline" and action == "validate":
            return self._validate_pipeline(arguments.source)
        if group == "pipeline" and action == "show":
            definition = _load_pipeline_source(arguments.source)
            return CLIResult({"pipeline": canonical_definition_document(definition)})
        if (
            group == "pipeline"
            and action == "enqueue"
            and self._resolved_run_creator is not None
        ):
            return self._enqueue_pipeline(arguments)
        if (
            group == "pipeline"
            and action == "execute"
            and self._resolved_run_creator is not None
            and self._run_executor is not None
        ):
            return self._execute_pipeline(arguments)
        if group == "run" and action == "create" and self._run_creator is not None:
            return self._create_run(arguments)
        if group == "run" and action == "execute" and self._run_executor is not None:
            return self._execute_run(arguments)
        if group == "run" and action == "cancel":
            return self._cancel_run(arguments)
        if group == "run" and action in {"configuration export", "export"}:
            return self._export(action, arguments)
        if group == "run" and action == "artifacts" and self._run_artifacts is not None:
            identifier = str(RunId.parse(arguments.run_id))
            artifacts = self._run_artifacts(identifier, arguments.locations)
            return CLIResult({"artifacts": list(artifacts)})
        if group != "run" or action not in {
            "inputs",
            "outputs",
            "status",
            "tasks",
        }:
            return CLIResult(
                {"action": action, "group": group},
                exit_code=2,
                message=f"local backend does not support {group} {action} yet",
            )
        return self._read_run(action, arguments)

    def _read_dispatch(
        self,
        action: str,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        dispatches = self._dispatches
        assert dispatches is not None
        identifier = DispatchId.parse(arguments.dispatch_id)
        if action == "show":
            value = dispatches.get(identifier)
            return CLIResult({"dispatch": dispatch_document(value)["dispatch"]})
        if action == "cancel":
            value = dispatches.cancel(identifier)
            return CLIResult({"dispatch": dispatch_document(value)["dispatch"]})
        if action == "wait":
            value = dispatches.get(identifier)
            while not value.state.terminal:
                sleep(0.05)
                value = dispatches.get(identifier)
            return CLIResult({"dispatch": dispatch_document(value)["dispatch"]})
        if action == "retry" and self._run_executor is not None:
            original = dispatches.get(identifier)
            retried = self._run_executor.execute(
                original.run_identifier,
                selection=TaskSelection(
                    all=original.selection.all,
                    all_remaining=original.selection.all_remaining,
                    labels=original.selection.labels,
                    nodes=original.selection.nodes,
                    only_failed=True,
                    procedures=original.selection.procedures,
                    records=original.selection.records,
                ),
                dependency_policy=original.dependency_policy,
            )
            return CLIResult({"dispatch": dispatch_document(retried)["dispatch"]})
        return CLIResult(
            {"action": action, "group": "dispatch"},
            exit_code=2,
            message=f"local backend does not support dispatch {action} yet",
        )

    def _cancel_run(self, arguments: argparse.Namespace) -> CLIResult:
        run = self._canceller.cancel_run(RunId.parse(arguments.run_id))
        return CLIResult({"run_id": str(run.identifier), "status": run.state.value})

    def _execute_run(self, arguments: argparse.Namespace) -> CLIResult:
        return self._execute_run_identifier(
            RunId.parse(arguments.run_id),
            arguments,
        )

    def _execute_run_identifier(
        self,
        run_identifier: RunId,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        executor = cast(RunExecutor, self._run_executor)
        selection = TaskSelection(
            all=arguments.all,
            all_remaining=arguments.all_remaining,
            labels=tuple(arguments.label),
            nodes=tuple(arguments.node),
            only_failed=arguments.only_failed,
            only_incomplete=not arguments.all,
            procedures=tuple(arguments.procedure),
            records=tuple(arguments.record),
        )
        dependency_policy = (
            DependencyPolicy.INCLUDE_MISSING_UPSTREAM
            if arguments.include_missing_upstream
            else DependencyPolicy.SELECTED_ONLY
        )
        dispatch = executor.execute(
            run_identifier,
            selection=selection,
            dependency_policy=dependency_policy,
        )
        return CLIResult({"dispatch": dispatch_document(dispatch)["dispatch"]})

    def _enqueue_pipeline(self, arguments: argparse.Namespace) -> CLIResult:
        run = self._create_resolved_run(arguments)
        return CLIResult({"run_id": str(run.identifier), "status": run.state.value})

    def _execute_pipeline(self, arguments: argparse.Namespace) -> CLIResult:
        run = self._create_resolved_run(arguments)
        return self._execute_run_identifier(run.identifier, arguments)

    def _create_resolved_run(self, arguments: argparse.Namespace) -> PipelineRun:
        if arguments.records_from is None or arguments.config is None:
            raise ValueError("pipeline execution requires --records-from and --config")
        payload = yaml.safe_load(Path(arguments.config).read_text(encoding="utf-8"))
        configuration = to_json_value(payload)
        if not isinstance(configuration, dict):
            raise TypeError("pipeline resolver configuration must be a mapping")
        creator = cast(ResolvedRunCreator, self._resolved_run_creator)
        return creator.create(
            _load_pipeline_source(arguments.source),
            resolver_identifier=arguments.records_from,
            configuration=configuration,
            metadata={"selection": _selection_document(arguments)},
        )

    def _create_run(self, arguments: argparse.Namespace) -> CLIResult:
        if arguments.input_set is None:
            raise ValueError("run create requires --input-set")
        creator = cast(RunCreator, self._run_creator)
        run = creator.create(
            _load_pipeline_source(arguments.source),
            input_set=arguments.input_set,
            metadata={"selection": _selection_document(arguments)},
        )
        return CLIResult({"run_id": str(run.identifier), "status": run.state.value})

    def _input_set(
        self,
        action: str,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        store = cast(InputSetStore, self._input_sets)
        if action == "create":
            return self._create_input_set(store, arguments)
        if action == "list":
            return CLIResult(
                {"input_sets": [_input_set_summary(value) for value in store.list()]}
            )
        value = store.get(arguments.input_set_id)
        if action == "artifacts":
            return self._input_set_artifacts(value, arguments.locations)
        if action == "show":
            return CLIResult(_input_set_document(value))
        if action == "export":
            return self._export_input_set(value, arguments.output)
        return CLIResult(
            {"action": action, "group": "input-set"},
            exit_code=2,
            message=f"local backend does not support input-set {action} yet",
        )

    def _input_set_artifacts(
        self,
        value: InputSet,
        include_locations: bool,
    ) -> CLIResult:
        bindings = tuple(
            InputSetArtifactBinding(
                input_set_id=value.identity,
                record_key=str(record.key),
                input_field=name,
                artifact_identity=identity,
            )
            for record in value.records
            for name, identities in record.inputs.items()
            for identity in identities
        )
        query = ArtifactQueryService(
            bindings=(),
            location_lookup=self._artifact_locations,
            input_set_bindings=bindings,
        )
        return CLIResult(
            {
                "artifacts": [
                    {
                        **asdict(result.binding),
                        "locations": [
                            to_json_value(location) for location in result.locations
                        ],
                    }
                    for result in query.query_input_set(
                        value.identity,
                        include_locations=include_locations,
                    )
                ]
            }
        )

    def _create_input_set(
        self,
        store: InputSetStore,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        if arguments.identifier is None:
            raise ValueError("input-set create requires --identifier")
        source = arguments.source
        content = sys.stdin.read() if source is None else Path(source).read_text()
        value = store.create(
            identifier=InputSetIdentifier(arguments.identifier),
            records=load_input_records_ndjson(
                content,
                source="<stdin>" if source is None else source,
            ),
            metadata=_labels(arguments.label),
        )
        return CLIResult(_input_set_summary(value))

    def _export_input_set(self, value: InputSet, destination: str) -> CLIResult:
        payload = "".join(
            canonical_json(cast(Any, _input_record_document(record))) + "\n"
            for record in value.records
        )
        output = Path(destination)
        output.write_text(payload, encoding="utf-8")
        return CLIResult({"output": str(output)})

    def _validate_pipeline(self, source: str) -> CLIResult:
        definition = _load_pipeline_source(source)
        artifact_catalogs, procedure_catalogs = _installed_validation_catalogs()
        try:
            validate_pipeline_definition(
                definition,
                artifact_catalogs,
                procedure_catalogs,
            )
        except PipelineCompilationError as error:
            diagnostics = [
                {
                    "code": diagnostic.code,
                    "message": diagnostic.message,
                    "path": diagnostic.path,
                }
                for diagnostic in error.diagnostics
            ]
            return CLIResult(
                {"diagnostics": diagnostics, "valid": False},
                exit_code=2,
            )
        return CLIResult({"diagnostics": [], "valid": True})

    def _list_pipelines(self) -> CLIResult:
        discovery = discover_pipeline_catalogs()
        pipelines = [
            {
                "identifier": registration.identifier,
                "kind": registration.kind,
                "location": registration.location,
            }
            for registration in sorted(
                discovery.catalog.registrations,
                key=lambda item: item.identifier,
            )
        ]
        diagnostics = [
            {
                "definition_identifier": diagnostic.definition_identifier,
                "distribution": diagnostic.distribution,
                "distribution_version": diagnostic.distribution_version,
                "entry_point": diagnostic.entry_point,
                "error_chain": list(diagnostic.error_chain),
                "location": diagnostic.location,
                "registration_kind": diagnostic.registration_kind,
            }
            for diagnostic in discovery.diagnostics
        ]
        return CLIResult({"diagnostics": diagnostics, "pipelines": pipelines})

    def _read_run(
        self,
        action: str,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        identifier = RunId.parse(arguments.run_id)
        view = self._runs.get(identifier)
        if action == "status":
            return CLIResult(
                {
                    "run_id": view.run_id,
                    "status": view.status,
                    "status_counts": dict(view.status_counts),
                }
            )
        if action in {"inputs", "outputs"}:
            document = pipeline_run_document(self._store.get_run(identifier))
            if action == "inputs":
                return CLIResult({"inputs": document["inputs"]})
            return CLIResult(
                {
                    "expected": document["expected_outputs"],
                    "produced": list(view.outputs),
                }
            )
        return CLIResult(
            {
                "tasks": [
                    {
                        **asdict(task),
                        "expected_output_fields": list(task.expected_output_fields),
                    }
                    for task in view.tasks
                ]
            }
        )

    def _export(
        self,
        action: str,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        identifier = RunId.parse(arguments.run_id)
        payload = (
            self._exporter.configuration_json(identifier)
            if action == "configuration export"
            else self._exporter.run_bundle_json(identifier)
        )
        output = Path(arguments.output)
        output.write_text(payload.rstrip("\n") + "\n", encoding="utf-8")
        return CLIResult({"output": str(output)})


class _EnvironmentBackend:
    def execute(
        self,
        group: str,
        action: str,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        import os
        import time
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4

        from provium import ProcedureExecutor
        from provium_pipeline.artifact.filesystem import FilesystemArtifactStore
        from provium_pipeline.artifact.service import ArtifactImportService
        from provium_pipeline.artifact.sqlite_index import SQLiteArtifactIndex
        from provium_pipeline.dispatch_creation import DispatchCreationService
        from provium_pipeline.dispatch_models import RetryPolicy
        from provium_pipeline.dispatch_worker import SerialDispatchWorker
        from provium_pipeline.input_resolver import discover_input_record_resolvers
        from provium_pipeline.local_task_attempt import LocalTaskAttemptExecutor
        from provium_pipeline.resolved_run_creation import ResolvedRunCreationService
        from provium_pipeline.run_execution import LocalRunExecutor
        from provium_pipeline.sqlite_attempts import SQLiteAttemptLeaseManager
        from provium_pipeline.sqlite_dispatch_store import SQLiteDispatchStore
        from provium_pipeline.sqlite_execution_store import SQLiteExecutionStore
        from provium_pipeline.sqlite_input_sets import SQLiteInputSetStore
        from provium_pipeline.task_executor import (
            PreparedProcedure,
            PreparedProcedureCache,
            execute_prepared_invocation,
        )
        from provium_pipeline.task_invocation_builder import FrozenTaskInvocationBuilder
        from provium_pipeline.task_outputs import SQLiteTaskOutputStore

        database = Path(
            os.environ.get(
                "PROVIUM_PIPELINE_DATABASE",
                ".provium/pipeline.sqlite3",
            )
        )
        database.parent.mkdir(parents=True, exist_ok=True)
        data_root = database.parent
        runs = SQLiteExecutionStore(database)
        dispatches = SQLiteDispatchStore(database)
        input_sets = SQLiteInputSetStore(database)
        artifact_index = SQLiteArtifactIndex(database)
        outputs = SQLiteTaskOutputStore(database)
        attempts = SQLiteAttemptLeaseManager(database)

        def output_observations(run_identifier: object) -> Sequence[object]:
            return tuple(
                to_json_value(value)
                for value in outputs.list_for_run(cast(RunId, run_identifier))
            )

        def run_artifact_observations(
            run_identifier: str,
            include_locations: bool,
        ) -> Sequence[object]:
            bindings = tuple(
                RunArtifactBinding(
                    artifact_identity=artifact_identity,
                    run_id=str(output.run_identifier),
                    record_key=str(output.record_key),
                    node_id=output.node_identifier,
                    output_field=output_field,
                    disposition="produced",
                    origin_run_id=str(output.run_identifier),
                    origin_task_id=str(output.task_identifier),
                )
                for output in outputs.list_for_run(RunId.parse(run_identifier))
                for output_field, artifact_identity in output.outputs.items()
                if artifact_identity is not None
            )
            service = ArtifactQueryService(
                bindings=bindings,
                location_lookup=artifact_index.get_active_locations,
            )
            return tuple(
                to_json_value(result)
                for result in service.query(
                    ArtifactQuery(
                        run_id=run_identifier,
                        include_locations=include_locations,
                    )
                )
            )

        artifact_store = FilesystemArtifactStore(
            identifier="local",
            root=data_root / "artifacts",
        )
        artifact_catalogs, procedure_catalogs = _installed_validation_catalogs()
        run_creator = RunCreationService(
            compiler=PipelineCompiler(artifact_catalogs, procedure_catalogs),
            input_sets=input_sets,
            artifact_index=artifact_index,
            runs=runs,
        )
        resolved_run_creator = ResolvedRunCreationService(
            resolvers=discover_input_record_resolvers(),
            artifact_index=artifact_index,
            runs=run_creator,
        )
        invocation_builder = FrozenTaskInvocationBuilder(
            runs=runs,
            procedures=procedure_catalogs,
            artifact_catalogs=artifact_catalogs,
            artifact_index=artifact_index,
            artifact_store=artifact_store,
            outputs=outputs,
            workspace=data_root / "workspaces",
        )
        prepared_cache: PreparedProcedureCache[PreparedProcedure] = (
            PreparedProcedureCache()
        )
        procedure_executor = ProcedureExecutor()
        importer_service = ArtifactImportService(
            store=artifact_store,
            index=artifact_index,
        )

        class _IdentityImporter:
            def import_artifact(self, path: Path) -> str:
                return importer_service.import_artifact(path).identity

        task_executor = LocalTaskAttemptExecutor(
            builder=invocation_builder,
            invoke=lambda invocation, materializations: execute_prepared_invocation(
                invocation,
                executor=procedure_executor,
                cache=prepared_cache,
                materializations=materializations,
            ),
            importer=_IdentityImporter(),
            outputs=outputs,
        )

        def clock() -> datetime:
            return datetime.now(UTC)

        def execute_task(task: Any, lease: Any) -> None:
            task_executor.execute(task, lease)

        worker = SerialDispatchWorker(
            executions=runs,
            attempts=attempts,
            execute_task=execute_task,
            clock=clock,
            token_factory=lambda: uuid4().hex,
            worker_identity=f"local-{os.getpid()}",
            lease_ttl=timedelta(minutes=5),
            wait_for_work=lambda: time.sleep(0.01),
            is_retryable=lambda error: True,
            wait_until=lambda eligible_at: time.sleep(
                max(0.0, (eligible_at - clock()).total_seconds())
            ),
        )
        dispatch_creator = DispatchCreationService(
            executions=runs,
            dispatches=dispatches,
            clock=clock,
        )
        run_executor = LocalRunExecutor(
            creator=dispatch_creator,
            dispatches=dispatches,
            runs=runs,
            run_dispatch=worker.run,
            retry_policy=RetryPolicy(max_attempts=3),
        )
        try:
            return LocalCLIBackend(
                cast(RunLookup, runs),
                input_sets=cast(InputSetStore, input_sets),
                dispatches=dispatches,
                artifact_locations=artifact_index.get_active_locations,
                run_creator=run_creator,
                resolved_run_creator=resolved_run_creator,
                run_executor=run_executor,
                run_outputs=output_observations,
                run_artifacts=run_artifact_observations,
            ).execute(
                group,
                action,
                arguments,
            )
        finally:
            prepared_cache.close()


_backend: ContextVar[PipelineCLIBackend] = ContextVar(
    "provium_pipeline_cli_backend",
    default=_EnvironmentBackend(),
)


@contextmanager
def use_cli_backend(backend: PipelineCLIBackend) -> Generator[None, None, None]:
    """Temporarily bind a command backend for an invocation context."""
    token = _backend.set(backend)
    try:
        yield
    finally:
        _backend.reset(token)


def _error_result(error_type: str, message: str) -> CLIResult:
    return CLIResult(
        {"error": {"message": message, "type": error_type}},
        exit_code=2,
        message=message,
    )


def _execute_backend(
    group: str,
    arguments: argparse.Namespace,
) -> CLIResult:
    try:
        return _backend.get().execute(group, arguments.cli_action, arguments)
    except ValueError as error:
        run_id = getattr(arguments, "run_id", None)
        message = str(error) if run_id is None else f"invalid run identifier: {run_id}"
        return _error_result("invalid_argument", message)
    except KeyError as error:
        return _error_result("not_found", str(error.args[0]))
    except (OSError, sqlite3.Error) as error:
        return _error_result(
            "storage_error",
            f"pipeline storage unavailable: {error}",
        )


class _PipelineCommand(Command):
    group: ClassVar[str]

    def execute(self, arguments: argparse.Namespace) -> int:
        result = _execute_backend(self.group, arguments)
        if arguments.output_format == "json":
            sys.stdout.write(
                canonical_json(
                    cast(
                        Any,
                        {
                            "schema": "provium.pipeline-cli/v1",
                            "data": dict(result.data),
                        },
                    )
                )
                + "\n"
            )
        elif result.message is not None:
            sys.stdout.write(result.message + "\n")
        else:
            for key, value in sorted(result.data.items()):
                sys.stdout.write(f"{key}: {value}\n")
        return result.exit_code


class PipelineCommand(_PipelineCommand):
    name = "pipeline"
    group = "pipeline"
    help = "Validate, inspect, and execute pipelines"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="pipeline_action", required=True)
        descriptions = {
            "validate": "Validate a pipeline definition",
            "show": "Show a canonical pipeline definition",
            "execute": "Execute a pipeline from resolved input records",
            "enqueue": "Create a run from resolved input records",
        }
        for action in ("validate", "show", "execute", "enqueue"):
            command = actions.add_parser(action, help=descriptions[action])
            command.add_argument("source")
            if action in {"execute", "enqueue"}:
                command.add_argument("--records-from")
                command.add_argument("--config")
                _add_selection_flags(command)
            _finish(command, action)
        _finish(actions.add_parser("list", help="List installed pipelines"), "list")


class InputSetCommand(_PipelineCommand):
    name = "input-set"
    group = "input-set"
    help = "Create and inspect immutable input sets"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="input_set_action", required=True)
        create = actions.add_parser("create", help="Create an immutable input set")
        create.add_argument("source", nargs="?")
        create.add_argument("--identifier")
        create.add_argument("--label", action="append", default=[])
        _finish(create, "create")
        _finish(actions.add_parser("list", help="List immutable input sets"), "list")
        descriptions = {
            "show": "Show an immutable input set",
            "export": "Export frozen input records",
            "artifacts": "List artifacts bound to an input set",
        }
        for action in ("show", "export", "artifacts"):
            command = actions.add_parser(action, help=descriptions[action])
            command.add_argument("input_set_id")
            if action == "export":
                command.add_argument("--output", required=True)
            if action == "artifacts":
                command.add_argument("--locations", action="store_true")
            _finish(command, action)


class RunCommand(_PipelineCommand):
    name = "run"
    group = "run"
    help = "Create, execute, and inspect pipeline runs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="run_action", required=True)
        create = actions.add_parser("create", help="Create a reproducible run")
        create.add_argument("source")
        create.add_argument("--input-set")
        _add_selection_flags(create)
        _finish(create, "create")
        execute = actions.add_parser("execute", help="Execute selected run tasks")
        execute.add_argument("run_id")
        _add_selection_flags(execute)
        _finish(execute, "execute")
        descriptions = {
            "status": "Show run status",
            "tasks": "List tasks in a run",
            "inputs": "List frozen run inputs",
            "outputs": "List produced run outputs",
            "artifacts": "List artifacts bound to a run",
            "cancel": "Cancel a nonterminal run",
        }
        for action in ("status", "tasks", "inputs", "outputs", "artifacts", "cancel"):
            command = actions.add_parser(action, help=descriptions[action])
            command.add_argument("run_id")
            if action == "artifacts":
                command.add_argument("--locations", action="store_true")
            _finish(command, action)
        export = actions.add_parser("export", help="Export a complete run bundle")
        export.add_argument("run_id")
        export.add_argument("--output", required=True)
        _finish(export, "export")
        configuration = actions.add_parser(
            "configuration",
            help="Inspect resolved run configuration",
        )
        configuration_actions = configuration.add_subparsers(
            dest="configuration_action", required=True
        )
        configuration_export = configuration_actions.add_parser(
            "export",
            help="Export resolved run configuration",
        )
        configuration_export.add_argument("run_id")
        configuration_export.add_argument("--output", required=True)
        _finish(configuration_export, "configuration export")


class DispatchCommand(_PipelineCommand):
    name = "dispatch"
    group = "dispatch"
    help = "Inspect and control dispatches"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="dispatch_action", required=True)
        descriptions = {
            "show": "Show dispatch state",
            "wait": "Wait for terminal state",
            "cancel": "Cancel a nonterminal dispatch",
            "retry": "Retry failed tasks",
        }
        for action in ("show", "wait", "cancel", "retry"):
            command = actions.add_parser(action, help=descriptions[action])
            command.add_argument("dispatch_id")
            _finish(command, action)


def _finish(parser: argparse.ArgumentParser, action: str) -> None:
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
    )
    parser.set_defaults(cli_action=action)


def _add_selection_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--all-remaining", action="store_true")
    parser.add_argument("--node", action="append", default=[])
    parser.add_argument("--procedure", action="append", default=[])
    parser.add_argument("--record", action="append", default=[])
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--only-failed", action="store_true")
    parser.add_argument("--include-missing-upstream", action="store_true")


catalog = CommandCatalog()
for command_type in (PipelineCommand, InputSetCommand, RunCommand, DispatchCommand):
    catalog.register(command_type)

cli_plugin = CLIPlugin(
    api_version=CLI_PLUGIN_API_VERSION,
    implementation="provium-pipeline",
    catalog=catalog,
)


__all__ = [
    "CLIResult",
    "DispatchCommand",
    "InputSetCommand",
    "LocalCLIBackend",
    "PipelineCLIBackend",
    "PipelineCommand",
    "RunCommand",
    "RunExporter",
    "cli_plugin",
    "use_cli_backend",
]
