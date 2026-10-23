import argparse
import json
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from provium.cli import run
from test_commands import config
from test_store import make_artifact

from provium_pipeline import PipelineDefinition, cli, definitions
from provium_pipeline.cli.arguments import assignments
from provium_pipeline.definitions import load_definition

DSN = os.environ.get("PROVIUM_TEST_DATABASE")


@pytest.mark.parametrize("values", [["x"], ["=x"], ["x="], ["x=a", "x=b"]])
def test_invalid_input_assignments(values):
    with pytest.raises(ValueError):
        assignments(values)


def test_definition_loading(monkeypatch):
    definition = PipelineDefinition({}, {})
    monkeypatch.setitem(
        sys.modules, "application", SimpleNamespace(definition=definition, wrong=42)
    )
    assert load_definition("p", "application:definition") is definition
    with pytest.raises(ValueError):
        load_definition("p", "application")
    with pytest.raises(TypeError):
        load_definition("p", "application:wrong")
    entry = SimpleNamespace(load=lambda: definition)
    monkeypatch.setattr(definitions, "entry_points", lambda **kw: [entry])
    assert load_definition("p", None) is definition
    monkeypatch.setattr(definitions, "entry_points", lambda **kw: [entry, entry])
    with pytest.raises(ValueError, match="duplicate"):
        load_definition("p", None)


def test_cli_validation_profiles_and_errors(tmp_path, monkeypatch):
    path = config(tmp_path)
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text("kind: ComputeProfile\nname: local\n")
    args = ["-f", path, "--store", str(tmp_path / "store")]
    assert (
        run(["pipeline", "validate", *args, "-f", str(profiles), "--profile", "local"])
        == 0
    )
    assert run(["pipeline", "inspect", *args]) == 0
    assert run(["pipeline", "validate", *args, "--profile", "missing"]) == 2
    assert run(["pipeline", "validate", *args, "--workers", "invalid"]) == 2
    assert run(["pipeline", "validate"]) == 2
    monkeypatch.delenv("PROVIUM_DATABASE_URL", raising=False)
    assert run(["pipeline", "run", "registered"]) == 2

    parser = argparse.ArgumentParser()
    command = cli.PipelineCommand()
    command.configure(parser)
    parsed = parser.parse_args(["validate", *args])
    assert command.execute(parsed) == 0
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["unsupported"])
    assert error.value.code == 2


@pytest.mark.skipif(not DSN, reason="requires PostgreSQL")
def test_persistent_commands_and_invalid_combinations(tmp_path, capsys):
    name = "p-" + uuid4().hex
    path = tmp_path / "bundle.yaml"
    path.write_text(
        f"kind: PipelineConfiguration\nname: {name}\n"
        "inputs: {x: provium_text_pipeline_example.DocumentV1}\nnodes: {}\n"
        "outputs: {result: '$inputs.x'}\n"
        f"---\nkind: ComputeProfile\nname: {name}\n"
    )
    common = ["--database", DSN, "--store", str(tmp_path / "store")]
    assert run(["pipeline", "register", *common]) == 2
    assert run(["pipeline", "register", *common, "-f", str(path)]) == 0
    assert run(["pipeline", "run", name, *common, "-f", str(path)]) == 2
    source = make_artifact(tmp_path / "source.pa")
    assert run(["pipeline", "enqueue", name, *common, "--input", f"x={source}"]) == 0
    capsys.readouterr()
    assert run(["pipeline", "run", name, *common, "--profile", name, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["state"] == "completed"
    assert run(["pipeline", "status", name, *common]) == 0
    assert run(["pipeline", "retry", name, *common]) == 0
    assert run(["pipeline", "run", name, *common, "--input", f"x={source}"]) == 2
