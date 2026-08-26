import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

from pytest import CaptureFixture, MonkeyPatch

from provium.cli import CLI_PLUGIN_API_VERSION
from provium_pipeline import cli as pipeline_cli
from provium_pipeline.cli import (
    CLIResult,
    LocalCLIBackend,
    RunExporter,
    cli_plugin,
    use_cli_backend,
)
from provium_pipeline.run_query import RunLookup


class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def execute(
        self,
        group: str,
        action: str,
        arguments: argparse.Namespace,
    ) -> CLIResult:
        self.calls.append((group, action))
        return CLIResult({"action": action, "group": group})


def command_arguments(group: str, arguments: list[str]) -> argparse.Namespace:
    command = cli_plugin.catalog.resolve(group)()
    parser = argparse.ArgumentParser()
    command.configure(parser)
    return parser.parse_args(arguments)


def test_cli_plugin_registers_complete_top_level_command_matrix() -> None:
    assert cli_plugin.api_version == CLI_PLUGIN_API_VERSION
    assert set(cli_plugin.catalog.commands) == {
        "dispatch",
        "input-set",
        "pipeline",
        "run",
    }
    matrix: Mapping[str, tuple[list[str], ...]] = {
        "pipeline": (
            ["validate", "pipeline.yaml"],
            ["show", "pipeline.yaml"],
            ["execute", "pipeline.yaml", "--all"],
            ["enqueue", "pipeline.yaml", "--node", "detect"],
            ["list"],
        ),
        "input-set": (
            ["create"],
            ["list"],
            ["show", "set-1"],
            ["export", "set-1", "--output", "inputs.ndjson"],
            ["artifacts", "set-1", "--locations"],
        ),
        "run": (
            ["create", "pipeline.yaml", "--record", "record-1"],
            ["execute", "run-1", "--only-failed"],
            ["status", "run-1"],
            ["tasks", "run-1"],
            ["inputs", "run-1"],
            ["outputs", "run-1"],
            ["artifacts", "run-1"],
            ["configuration", "export", "run-1", "--output", "resolved.json"],
            ["export", "run-1", "--output", "bundle.json"],
            ["cancel", "run-1"],
        ),
        "dispatch": (
            ["show", "dispatch-1"],
            ["wait", "dispatch-1"],
            ["cancel", "dispatch-1"],
            ["retry", "dispatch-1"],
        ),
    }
    for group, invocations in matrix.items():
        for invocation in invocations:
            command_arguments(group, invocation)


def test_cli_executes_backend_and_emits_versioned_json(
    capsys: CaptureFixture[str],
) -> None:
    backend = RecordingBackend()
    command = cli_plugin.catalog.resolve("run")()
    arguments = command_arguments("run", ["status", "run-1", "--format", "json"])

    with use_cli_backend(backend):
        assert command.execute(arguments) == 0

    document = json.loads(capsys.readouterr().out)
    assert document == {
        "data": {"action": "status", "group": "run"},
        "schema": "provium.pipeline-cli/v1",
    }
    assert backend.calls == [("run", "status")]


def test_cli_uses_environment_database_and_human_output(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "pipeline.sqlite3"
    monkeypatch.setenv("PROVIUM_PIPELINE_DATABASE", str(database))
    pipeline_command = cli_plugin.catalog.resolve("pipeline")()
    list_arguments = command_arguments("pipeline", ["list"])

    assert pipeline_command.execute(list_arguments) == 2
    assert capsys.readouterr().out == (
        "local backend does not support pipeline list yet\n"
    )
    assert database.exists()

    backend = RecordingBackend()
    run_command = cli_plugin.catalog.resolve("run")()
    status_arguments = command_arguments("run", ["status", str(UUID(int=1))])
    with use_cli_backend(backend):
        assert run_command.execute(status_arguments) == 0
    assert capsys.readouterr().out == "action: status\ngroup: run\n"


def test_cli_reports_invalid_run_identifier_as_versioned_json(
    capsys: CaptureFixture[str],
) -> None:
    command = cli_plugin.catalog.resolve("run")()
    arguments = command_arguments(
        "run",
        ["status", "not-a-uuid", "--format", "json"],
    )

    assert command.execute(arguments) == 2
    assert json.loads(capsys.readouterr().out) == {
        "data": {
            "error": {
                "message": "invalid run identifier: not-a-uuid",
                "type": "invalid_argument",
            }
        },
        "schema": "provium.pipeline-cli/v1",
    }


def test_cli_reports_missing_run_and_storage_failures(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    command = cli_plugin.catalog.resolve("run")()
    missing_id = str(UUID(int=9))
    database = tmp_path / "pipeline.sqlite3"
    monkeypatch.setenv("PROVIUM_PIPELINE_DATABASE", str(database))

    missing = command_arguments("run", ["status", missing_id, "--format", "json"])
    assert command.execute(missing) == 2
    assert json.loads(capsys.readouterr().out)["data"] == {
        "error": {
            "message": f"unknown run identifier: {missing_id}",
            "type": "not_found",
        }
    }

    monkeypatch.setenv("PROVIUM_PIPELINE_DATABASE", str(tmp_path))
    unavailable = command_arguments("run", ["status", missing_id])
    assert command.execute(unavailable) == 2
    assert capsys.readouterr().out.startswith("pipeline storage unavailable: ")


def test_local_cli_backend_reads_durable_run_status_and_tasks(
    monkeypatch: MonkeyPatch,
) -> None:
    run_id = str(UUID(int=1))

    class Store:
        def get_run(self, identifier: object) -> object:
            assert str(identifier) == run_id
            return SimpleNamespace(
                identifier=identifier,
                state=SimpleNamespace(value="running"),
                inputs=SimpleNamespace(records=()),
                expected_outputs=(SimpleNamespace(name="result"),),
            )

        def list_tasks(self, identifier: object) -> tuple[object, ...]:
            return (
                SimpleNamespace(
                    identifier=UUID(int=3),
                    record_key="record-1",
                    node_identifier="detect",
                    state=SimpleNamespace(value="ready"),
                    expected_output_fields=("detections",),
                ),
            )

    def run_document(run: object) -> dict[str, object]:
        return {
            "inputs": {"records": [{"key": "record-1"}]},
            "expected_outputs": [{"name": "result"}],
        }

    monkeypatch.setattr(pipeline_cli, "pipeline_run_document", run_document)
    backend = LocalCLIBackend(cast(RunLookup, Store()))
    status = backend.execute(
        "run",
        "status",
        argparse.Namespace(run_id=run_id),
    )
    tasks = backend.execute(
        "run",
        "tasks",
        argparse.Namespace(run_id=run_id),
    )
    inputs = backend.execute(
        "run",
        "inputs",
        argparse.Namespace(run_id=run_id),
    )
    outputs = backend.execute(
        "run",
        "outputs",
        argparse.Namespace(run_id=run_id),
    )

    assert inputs.data == {"inputs": {"records": [{"key": "record-1"}]}}
    assert outputs.data == {
        "expected": [{"name": "result"}],
        "produced": [],
    }
    assert status.data == {
        "run_id": run_id,
        "status": "running",
        "status_counts": {"ready": 1},
    }
    unsupported = backend.execute(
        "pipeline",
        "list",
        argparse.Namespace(),
    )

    assert unsupported.exit_code == 2
    assert unsupported.message == "local backend does not support pipeline list yet"
    assert tasks.data["tasks"] == [
        {
            "cache_disposition": None,
            "expected_output_fields": ["detections"],
            "node_id": "detect",
            "record_key": "record-1",
            "status": "ready",
            "task_id": str(UUID(int=3)),
        }
    ]


def test_local_cli_backend_writes_configuration_and_bundle_exports(
    tmp_path: Path,
) -> None:
    class Exporter:
        def configuration_json(self, identifier: object) -> str:
            return '{"setting":1}'

        def run_bundle_json(self, identifier: object) -> str:
            return '{"schema":"provium.run-bundle/v1"}'

    store = cast(RunLookup, object())
    backend = LocalCLIBackend(
        store,
        exporter=cast(RunExporter, Exporter()),
    )
    run_id = str(UUID(int=1))
    configuration = tmp_path / "configuration.json"
    bundle = tmp_path / "bundle.json"

    configuration_result = backend.execute(
        "run",
        "configuration export",
        argparse.Namespace(run_id=run_id, output=str(configuration)),
    )
    bundle_result = backend.execute(
        "run",
        "export",
        argparse.Namespace(run_id=run_id, output=str(bundle)),
    )

    assert configuration_result.data == {"output": str(configuration)}
    assert bundle_result.data == {"output": str(bundle)}
    assert configuration.read_text() == '{"setting":1}\n'
    assert bundle.read_text() == '{"schema":"provium.run-bundle/v1"}\n'
