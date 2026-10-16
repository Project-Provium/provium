import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_commands import config
from test_store import make_artifact

from provium_pipeline import PipelineDefinition, cli

DSN = os.environ.get("PROVIUM_TEST_DATABASE")


@pytest.mark.parametrize("values", [["x"], ["=x"], ["x="], ["x=a", "x=b"]])
def test_invalid_input_assignments(values):
    with pytest.raises(ValueError):
        cli.assignments(values)


def test_definition_loading(monkeypatch):
    definition = PipelineDefinition({}, {})
    monkeypatch.setitem(
        sys.modules, "application", SimpleNamespace(definition=definition, wrong=42)
    )
    assert cli.load_definition("p", "application:definition") is definition
    with pytest.raises(ValueError):
        cli.load_definition("p", "application")
    with pytest.raises(TypeError):
        cli.load_definition("p", "application:wrong")
    entry = SimpleNamespace(load=lambda: definition)
    monkeypatch.setattr(cli, "entry_points", lambda **kw: [entry])
    assert cli.load_definition("p", None) is definition
    monkeypatch.setattr(cli, "entry_points", lambda **kw: [entry, entry])
    with pytest.raises(ValueError, match="duplicate"):
        cli.load_definition("p", None)


def test_cli_validation_profiles_and_errors(tmp_path, monkeypatch):
    path = config(tmp_path)
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text("kind: ComputeProfile\nname: local\n")
    args = ["-f", path, "--store", str(tmp_path / "store")]
    assert cli.run(["validate", *args, "-f", str(profiles), "--profile", "local"]) == 0
    assert cli.run(["inspect", *args]) == 0
    assert cli.run(["validate", *args, "--profile", "missing"]) == 2
    assert cli.run(["validate", *args, "--workers", "invalid"]) == 2
    assert cli.run(["validate"]) == 2
    monkeypatch.delenv("PROVIUM_DATABASE_URL", raising=False)
    assert cli.run(["run", "registered"]) == 2

    parser = argparse.ArgumentParser()
    command = cli.PipelineCommand()
    command.configure(parser)
    parsed = parser.parse_args(["validate", *args])
    assert command.execute(parsed) == 0
    parsed.action = "unsupported"
    assert command.execute(parsed) == 2


def test_module_and_console_entrypoints(monkeypatch):
    monkeypatch.setattr(cli, "run", lambda: 0)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    import importlib

    imported = importlib.import_module("provium_pipeline.__main__")
    path = Path(cli.__file__).with_name("__main__.py")
    code = compile(path.read_text(), str(path), "exec")
    exec(code, {"__name__": "imported_main", "__package__": "provium_pipeline"})
    with pytest.raises(SystemExit) as result:
        exec(
            code,
            {
                "__name__": "__main__",
                "__package__": "provium_pipeline",
                "__spec__": imported.__spec__,
            },
        )
    assert result.value.code == 0


@pytest.mark.skipif(not DSN, reason="requires PostgreSQL")
def test_persistent_commands_and_invalid_combinations(tmp_path, capsys):
    name = "p-" + uuid4().hex
    path = tmp_path / "bundle.yaml"
    path.write_text(
        f"kind: PipelineConfiguration\nname: {name}\nnodes: {{}}\n"
        "outputs: {result: '$inputs.x'}\n"
        f"---\nkind: ComputeProfile\nname: {name}\n"
    )
    common = ["--database", DSN, "--store", str(tmp_path / "store")]
    assert cli.run(["register", *common]) == 2
    assert cli.run(["register", *common, "-f", str(path)]) == 0
    assert cli.run(["run", name, *common, "-f", str(path)]) == 2
    source = make_artifact(tmp_path / "source.pa")
    assert cli.run(["enqueue", name, *common, "--input", f"x={source}"]) == 0
    capsys.readouterr()
    assert cli.run(["run", name, *common, "--profile", name]) == 0
    assert json.loads(capsys.readouterr().out)[0]["state"] == "completed"
    assert cli.run(["status", name, *common]) == 0
    assert cli.run(["retry", name, *common]) == 0
    assert cli.run(["run", name, *common, "--input", f"x={source}"]) == 2
