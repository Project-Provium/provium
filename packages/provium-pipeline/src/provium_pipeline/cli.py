"""Core-CLI plugin exposing pipeline command families."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast

from provium.canonical import canonical_json
from provium.cli.catalog import CommandCatalog
from provium.cli.command import Command
from provium.cli.plugin import CLI_PLUGIN_API_VERSION, CLIPlugin

from .execution_codec import pipeline_run_document
from .exports import RunExportService
from .exports import RunLookup as ExportRunLookup
from .identifiers import RunId
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


class RunExporter(Protocol):
    def configuration_json(self, identifier: RunId) -> str: ...

    def run_bundle_json(self, identifier: RunId) -> str: ...


class LocalCLIBackend:
    """Read durable local run state through the execution-store contract."""

    def __init__(
        self,
        store: RunLookup,
        *,
        exporter: RunExporter | None = None,
    ) -> None:
        self._store = cast(ExportRunLookup, store)
        self._runs = RunQueryService(store)
        self._exporter = exporter or RunExportService(self._store)

    def execute(
        self,
        group: str,
        action: str,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        if group == "run" and action in {"configuration export", "export"}:
            return self._export(action, arguments)
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
                        "expected_output_fields": list(
                            task.expected_output_fields
                        ),
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

        from provium_pipeline.sqlite_execution_store import SQLiteExecutionStore

        database = Path(
            os.environ.get(
                "PROVIUM_PIPELINE_DATABASE",
                ".provium/pipeline.sqlite3",
            )
        )
        database.parent.mkdir(parents=True, exist_ok=True)
        return LocalCLIBackend(
            cast(RunLookup, SQLiteExecutionStore(database))
        ).execute(
            group,
            action,
            arguments,
        )


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
    except ValueError:
        value = getattr(arguments, "run_id", "")
        return _error_result(
            "invalid_argument",
            f"invalid run identifier: {value}",
        )
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
        for action in ("validate", "show", "execute", "enqueue"):
            command = actions.add_parser(action)
            command.add_argument("source")
            if action in {"execute", "enqueue"}:
                _add_selection_flags(command)
            _finish(command, action)
        _finish(actions.add_parser("list"), "list")


class InputSetCommand(_PipelineCommand):
    name = "input-set"
    group = "input-set"
    help = "Create and inspect immutable input sets"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="input_set_action", required=True)
        create = actions.add_parser("create")
        create.add_argument("source", nargs="?")
        _finish(create, "create")
        _finish(actions.add_parser("list"), "list")
        for action in ("show", "export", "artifacts"):
            command = actions.add_parser(action)
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
        create = actions.add_parser("create")
        create.add_argument("source")
        _add_selection_flags(create)
        _finish(create, "create")
        execute = actions.add_parser("execute")
        execute.add_argument("run_id")
        _add_selection_flags(execute)
        _finish(execute, "execute")
        for action in ("status", "tasks", "inputs", "outputs", "artifacts", "cancel"):
            command = actions.add_parser(action)
            command.add_argument("run_id")
            if action == "artifacts":
                command.add_argument("--locations", action="store_true")
            _finish(command, action)
        export = actions.add_parser("export")
        export.add_argument("run_id")
        export.add_argument("--output", required=True)
        _finish(export, "export")
        configuration = actions.add_parser("configuration")
        configuration_actions = configuration.add_subparsers(
            dest="configuration_action", required=True
        )
        configuration_export = configuration_actions.add_parser("export")
        configuration_export.add_argument("run_id")
        configuration_export.add_argument("--output", required=True)
        _finish(configuration_export, "configuration export")


class DispatchCommand(_PipelineCommand):
    name = "dispatch"
    group = "dispatch"
    help = "Inspect and control dispatches"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="dispatch_action", required=True)
        for action in ("show", "wait", "cancel", "retry"):
            command = actions.add_parser(action)
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
