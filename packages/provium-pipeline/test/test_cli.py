import argparse
import json
from collections.abc import Mapping

from pytest import CaptureFixture

from provium.cli import CLI_PLUGIN_API_VERSION
from provium_pipeline.cli import CLIResult, cli_plugin, use_cli_backend


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


def test_cli_human_output_and_unconfigured_backend_exit_code(
    capsys: CaptureFixture[str],
) -> None:
    command = cli_plugin.catalog.resolve("run")()
    arguments = command_arguments("run", ["status", "run-1"])

    assert command.execute(arguments) == 2
    assert capsys.readouterr().out == "pipeline CLI backend is not configured\n"

    backend = RecordingBackend()
    with use_cli_backend(backend):
        assert command.execute(arguments) == 0
    assert capsys.readouterr().out == "action: status\ngroup: run\n"
