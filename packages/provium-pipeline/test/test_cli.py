import argparse
import json
from collections.abc import Callable, Mapping
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

from pytest import CaptureFixture, MonkeyPatch, raises

from provium.artifact.catalog import ArtifactCatalog
from provium.cli import CLI_PLUGIN_API_VERSION
from provium.procedure.catalog import ProcedureCatalog
from provium_pipeline import cli as pipeline_cli
from provium_pipeline.cli import (
    CLIResult,
    LocalCLIBackend,
    RunExporter,
    cli_plugin,
    use_cli_backend,
)
from provium_pipeline.compiler.catalogs import (
    ArtifactCatalogCollection,
    ProcedureCatalogCollection,
)
from provium_pipeline.compiler.diagnostics import (
    PipelineCompilationDiagnostic,
    PipelineCompilationError,
)
from provium_pipeline.definition.models import PipelineDefinition
from provium_pipeline.run_query import RunLookup
from provium_pipeline.sqlite_input_sets import SQLiteInputSetStore


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

    assert pipeline_command.execute(list_arguments) == 0
    assert capsys.readouterr().out == "diagnostics: []\npipelines: []\n"
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
        "execute",
        argparse.Namespace(),
    )

    assert unsupported.exit_code == 2
    assert unsupported.message == "local backend does not support pipeline execute yet"
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


def test_pipeline_source_loader_supports_catalog_json_yaml_and_rejects_other(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog_definition = cast(
        PipelineDefinition, SimpleNamespace(identifier="catalog")
    )
    json_definition = cast(PipelineDefinition, SimpleNamespace(identifier="json"))
    yaml_definition = cast(PipelineDefinition, SimpleNamespace(identifier="yaml"))

    def get_definition(source: str) -> PipelineDefinition:
        assert source == "example"
        return catalog_definition

    def load_json(content: str, *, source: str) -> PipelineDefinition:
        assert content == "{}"
        assert source.endswith("pipeline.json")
        return json_definition

    def load_yaml(content: str, *, source: str) -> PipelineDefinition:
        assert content == "id: example"
        assert source.endswith("pipeline.yaml")
        return yaml_definition

    monkeypatch.setattr(
        pipeline_cli,
        "discover_pipeline_catalogs",
        lambda: SimpleNamespace(catalog=SimpleNamespace(get=get_definition)),
    )
    monkeypatch.setattr(pipeline_cli, "load_pipeline_json", load_json)
    monkeypatch.setattr(pipeline_cli, "load_pipeline_yaml", load_yaml)
    json_path = tmp_path / "pipeline.json"
    yaml_path = tmp_path / "pipeline.yaml"
    unsupported_path = tmp_path / "pipeline.txt"
    json_path.write_text("{}")
    yaml_path.write_text("id: example")
    unsupported_path.write_text("example")

    loader = cast(
        Callable[[str], PipelineDefinition],
        getattr(pipeline_cli, "_load_pipeline_source"),
    )
    assert loader("example") is catalog_definition
    assert loader(str(json_path)) is json_definition
    assert loader(str(yaml_path)) is yaml_definition
    try:
        loader(str(unsupported_path))
    except ValueError as error:
        assert str(error) == "unsupported pipeline source extension: .txt"
    else:
        raise AssertionError("unsupported extension was accepted")


def test_installed_validation_catalogs_wrap_core_discovery(
    monkeypatch: MonkeyPatch,
) -> None:
    artifact_catalog = ArtifactCatalog()
    procedure_catalog = ProcedureCatalog()
    monkeypatch.setattr(
        pipeline_cli,
        "discover_artifact_catalogs",
        lambda: artifact_catalog,
    )
    monkeypatch.setattr(
        pipeline_cli,
        "discover_procedure_catalogs",
        lambda: procedure_catalog,
    )
    discover = cast(
        Callable[
            [],
            tuple[ArtifactCatalogCollection, ProcedureCatalogCollection],
        ],
        getattr(pipeline_cli, "_installed_validation_catalogs"),
    )

    artifacts, procedures = discover()

    assert artifacts.catalogs == (artifact_catalog,)
    assert procedures.catalogs == (procedure_catalog,)


def test_local_cli_backend_validates_pipeline_and_reports_diagnostics(
    monkeypatch: MonkeyPatch,
) -> None:
    definition = cast(PipelineDefinition, object())
    def load_definition(source: str) -> PipelineDefinition:
        assert source == "example"
        return definition

    monkeypatch.setattr(
        pipeline_cli,
        "_load_pipeline_source",
        load_definition,
    )
    monkeypatch.setattr(
        pipeline_cli,
        "_installed_validation_catalogs",
        lambda: (object(), object()),
        raising=False,
    )
    calls: list[PipelineDefinition] = []

    def validate_valid(
        value: PipelineDefinition,
        artifact_catalogs: object,
        procedure_catalogs: object,
    ) -> None:
        calls.append(value)

    monkeypatch.setattr(
        pipeline_cli,
        "validate_pipeline_definition",
        validate_valid,
        raising=False,
    )
    backend = LocalCLIBackend(cast(RunLookup, object()))

    valid = backend.execute(
        "pipeline",
        "validate",
        argparse.Namespace(source="example"),
    )
    assert valid.data == {"diagnostics": [], "valid": True}
    assert calls == [definition]

    def validate_invalid(
        value: PipelineDefinition,
        artifact_catalogs: object,
        procedure_catalogs: object,
    ) -> None:
        raise PipelineCompilationError(
            (
                PipelineCompilationDiagnostic(
                    code="unknown-node",
                    path="outputs.result",
                    message="node 'missing' does not exist",
                ),
            )
        )

    monkeypatch.setattr(
        pipeline_cli,
        "validate_pipeline_definition",
        validate_invalid,
    )
    invalid = backend.execute(
        "pipeline",
        "validate",
        argparse.Namespace(source="example"),
    )
    assert invalid.exit_code == 2
    assert invalid.data == {
        "diagnostics": [
            {
                "code": "unknown-node",
                "message": "node 'missing' does not exist",
                "path": "outputs.result",
            }
        ],
        "valid": False,
    }


def test_local_cli_backend_shows_canonical_pipeline(
    monkeypatch: MonkeyPatch,
) -> None:
    definition = cast(PipelineDefinition, object())

    def load_source(source: str) -> PipelineDefinition:
        assert source == "example.pipeline"
        return definition

    def definition_document(value: PipelineDefinition) -> dict[str, object]:
        assert value is definition
        return {"id": "example.pipeline"}

    monkeypatch.setattr(pipeline_cli, "_load_pipeline_source", load_source)
    monkeypatch.setattr(
        pipeline_cli,
        "canonical_definition_document",
        definition_document,
    )
    backend = LocalCLIBackend(cast(RunLookup, object()))

    response = backend.execute(
        "pipeline",
        "show",
        argparse.Namespace(source="example.pipeline"),
    )

    assert response.data == {"pipeline": {"id": "example.pipeline"}}


def test_local_cli_backend_lists_discovered_pipelines(
    monkeypatch: MonkeyPatch,
) -> None:
    registrations = (
        SimpleNamespace(
            identifier="example.second",
            kind="python",
            location="example:second",
        ),
        SimpleNamespace(
            identifier="example.first",
            kind="yaml",
            location="first.yaml",
        ),
    )
    result = SimpleNamespace(
        catalog=SimpleNamespace(registrations=registrations),
        diagnostics=(
            SimpleNamespace(
                definition_identifier=None,
                distribution="broken",
                distribution_version="1.0",
                entry_point="broken",
                error_chain=("broken plugin",),
                location="broken:catalog",
                registration_kind="catalog",
            ),
        ),
    )
    monkeypatch.setattr(
        pipeline_cli,
        "discover_pipeline_catalogs",
        lambda: result,
        raising=False,
    )
    backend = LocalCLIBackend(cast(RunLookup, object()))

    response = backend.execute("pipeline", "list", argparse.Namespace())

    assert response.data == {
        "diagnostics": [
            {
                "definition_identifier": None,
                "distribution": "broken",
                "distribution_version": "1.0",
                "entry_point": "broken",
                "error_chain": ["broken plugin"],
                "location": "broken:catalog",
                "registration_kind": "catalog",
            }
        ],
        "pipelines": [
            {
                "identifier": "example.first",
                "kind": "yaml",
                "location": "first.yaml",
            },
            {
                "identifier": "example.second",
                "kind": "python",
                "location": "example:second",
            },
        ],
    }


def test_local_cli_backend_input_set_creation_validates_and_reads_stdin(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_sets = SQLiteInputSetStore(tmp_path / "pipeline.sqlite3")
    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        input_sets=input_sets,
    )
    content = (
        '{"inputs":{"document":["artifact-1"]},'
        '"key":"record-1","labels":{}}\n'
    )
    monkeypatch.setattr(pipeline_cli.sys, "stdin", StringIO(content))

    created = backend.execute(
        "input-set",
        "create",
        argparse.Namespace(
            identifier="stdin-v1",
            label=[],
            source=None,
        ),
    )
    assert created.data["identifier"] == "stdin-v1"

    with raises(ValueError, match="requires --identifier"):
        backend.execute(
            "input-set",
            "create",
            argparse.Namespace(identifier=None, label=[], source=None),
        )
    with raises(ValueError, match="expected KEY=VALUE"):
        backend.execute(
            "input-set",
            "create",
            argparse.Namespace(
                identifier="invalid-label",
                label=["invalid"],
                source=None,
            ),
        )
    unsupported = backend.execute(
        "input-set",
        "artifacts",
        argparse.Namespace(input_set_id="stdin-v1"),
    )
    assert unsupported.exit_code == 2
    assert unsupported.message == (
        "local backend does not support input-set artifacts yet"
    )


def test_local_cli_backend_creates_lists_shows_and_exports_input_sets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "records.ndjson"
    source.write_text(
        '{"inputs":{"document":["artifact-1"]},'
        '"key":"record-1","labels":{"split":"evaluation"}}\n'
    )
    output = tmp_path / "export.ndjson"
    input_sets = SQLiteInputSetStore(tmp_path / "pipeline.sqlite3")
    backend = LocalCLIBackend(
        cast(RunLookup, object()),
        input_sets=input_sets,
    )

    created = backend.execute(
        "input-set",
        "create",
        argparse.Namespace(
            identifier="evaluation-v1",
            label=["owner=quality"],
            source=str(source),
        ),
    )
    listed = backend.execute("input-set", "list", argparse.Namespace())
    shown = backend.execute(
        "input-set",
        "show",
        argparse.Namespace(input_set_id="evaluation-v1"),
    )
    exported = backend.execute(
        "input-set",
        "export",
        argparse.Namespace(input_set_id="evaluation-v1", output=str(output)),
    )

    assert created.data["identifier"] == "evaluation-v1"
    assert listed.data["input_sets"] == [created.data]
    shown_header = cast(Mapping[str, object], shown.data["input_set"])
    assert shown_header["metadata"] == {"owner": "quality"}
    assert shown.data["records"] == [
        {
            "inputs": {"document": ["artifact-1"]},
            "key": "record-1",
            "labels": {"split": "evaluation"},
        }
    ]
    assert exported.data == {"output": str(output)}
    assert output.read_text() == source.read_text()


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
