import argparse
import json
import os
from uuid import uuid4

import pytest
from provium.cli import run
from provium_pipeline import cli
from test_commands import config
from test_store import make_artifact

DSN = os.environ.get("PROVIUM_TEST_DATABASE")


def test_cli_validation_profiles_and_errors(tmp_path, monkeypatch):
    path = config(tmp_path)
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text("kind: ComputeProfile\nname: local\n")
    args = ["-f", path, "--store", str(tmp_path / "store")]
    assert (
        run(["dispatch", "validate", *args, "-f", str(profiles), "--profile", "local"])
        == 0
    )
    assert run(["pipeline", "inspect", *args]) == 0
    assert run(["dispatch", "validate", *args, "--profile", "missing"]) == 2
    assert run(["pipeline", "validate", *args, "--workers", "invalid"]) == 2
    assert run(["pipeline", "validate"]) == 2
    monkeypatch.delenv("PROVIUM_DATABASE_URL", raising=False)
    assert run(["dispatch", "run", "registered"]) == 2

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
    assert run(["dispatch", "register", *common]) == 2
    assert run(["dispatch", "register", *common, "-f", str(path)]) == 0
    assert run(["dispatch", "run", name, *common, "-f", str(path)]) == 2
    source = make_artifact(tmp_path / "source.pa")
    assert run(["dispatch", "enqueue", name, *common, "--input", f"x={source}"]) == 0
    capsys.readouterr()
    assert run(["dispatch", "run", name, *common, "--profile", name, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["state"] == "completed"
    assert run(["dispatch", "status", name, *common]) == 0
    assert run(["dispatch", "retry", name, *common]) == 0
    assert run(["dispatch", "run", name, *common, "--input", f"x={source}"]) == 2
