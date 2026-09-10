"""CLI plugin exercises the same API as library callers."""

import pytest
import yaml
from provium import ArtifactReadBinding, ProcedureCatalog, session
from provium.cli import run
from support.provium_test_pipeline.artifacts import TextArtifact
from test_execution import DEFINITIONS, definition

from provium_pipeline.cli import plugin


@pytest.fixture
def pipeline_file(tmp_path, monkeypatch):
    catalog = ProcedureCatalog()
    for item in DEFINITIONS.values():
        catalog.register(item)
    monkeypatch.setattr(
        "provium_pipeline.executor.discover_procedure_catalogs", lambda: catalog
    )
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(definition()))
    return path


def test_cli_validate_and_run(tmp_path, pipeline_file, capsys):
    command = ["pipeline", "validate", str(pipeline_file)]
    assert run(command, catalog=plugin.catalog) == 0
    assert "source" in capsys.readouterr().out
    command = [
        "pipeline",
        "run",
        str(pipeline_file),
        "--config",
        'text="C"',
        "--output",
        f"text={tmp_path / 'text.pa'}",
        "--output",
        f"report={tmp_path / 'report.pa'}",
    ]
    assert run(command, catalog=plugin.catalog) == 0
    with (
        session(),
        ArtifactReadBinding(TextArtifact, tmp_path / "text.pa").open() as reader,
    ):
        assert reader.read_text() == "<CCC>"
    assert capsys.readouterr().out.strip()


@pytest.mark.parametrize("contents", ["[]", "steps: [broken"])
def test_cli_reports_invalid_yaml(tmp_path, capsys, contents):
    path = tmp_path / "pipeline.yaml"
    path.write_text(contents)
    assert run(["pipeline", "validate", str(path)], catalog=plugin.catalog) == 1
    assert "pipeline" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--config", "bad"],
        ["--config", "text=A", "--config", "text=B"],
    ],
)
def test_cli_invalid_configuration(pipeline_file, arguments, capsys):
    command = ["pipeline", "validate", str(pipeline_file), *arguments]
    assert run(command, catalog=plugin.catalog) == 1
    assert capsys.readouterr().err


def test_cli_unknown_binding(pipeline_file, capsys):
    command = ["pipeline", "run", str(pipeline_file), "--input", "unknown=path"]
    assert run(command, catalog=plugin.catalog) == 1
    assert "unknown" in capsys.readouterr().err


def test_configuration_layers_and_retention(tmp_path, pipeline_file, capsys):
    yaml_config = tmp_path / "config.yaml"
    yaml_config.write_text("text: YAML")
    json_config = tmp_path / "config.json"
    json_config.write_text('{"text": "JSON"}')
    command = [
        "pipeline",
        "run",
        str(pipeline_file),
        "--config-file",
        str(yaml_config),
        "--config-file",
        str(json_config),
        "--config",
        "text=override",
        "--output",
        f"text={tmp_path / 'text.pa'}",
        "--output",
        f"report={tmp_path / 'report.pa'}",
        "--keep-intermediates",
        str(tmp_path / "debug"),
    ]
    assert run(command, catalog=plugin.catalog) == 0
    assert "Intermediates:" in capsys.readouterr().out
    with (
        session(),
        ArtifactReadBinding(TextArtifact, tmp_path / "text.pa").open() as reader,
    ):
        assert reader.read_text() == "<overrideoverrideoverride>"
