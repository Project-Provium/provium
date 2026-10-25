import json
from uuid import uuid4

from provium import cli
from test_management import DSN
from test_management import db as db


def test_management_cli(db, tmp_path, capsys):
    name = "p-" + uuid4().hex
    path = tmp_path / "bundle.yaml"
    path.write_text(
        f"kind: PipelineConfiguration\nname: {name}\n"
        "inputs: {}\nnodes: {}\noutputs: {}\n"
        f"---\nkind: ComputeProfile\nname: {name}\n"
    )
    common = ["--database", DSN]
    assert cli.run(["dispatch", "register", "-f", str(path), *common]) == 0
    db.enqueue(name, {})
    capsys.readouterr()
    assert cli.run(["dispatch", "list", "pipelines", *common]) == 0
    assert {"kind": "PipelineConfiguration", "name": name} in json.loads(
        capsys.readouterr().out
    )
    assert cli.run(["dispatch", "list", "profiles", *common]) == 0
    assert {"kind": "ComputeProfile", "name": name} in json.loads(
        capsys.readouterr().out
    )
    assert cli.run(["dispatch", "list", *common]) == 0
    capsys.readouterr()
    assert cli.run(["dispatch", "queue", name, *common]) == 0
    assert json.loads(capsys.readouterr().out)[0]["states"]["pending"] == 1
    assert cli.run(["dispatch", "queue", *common]) == 0
    capsys.readouterr()
    assert cli.run(["dispatch", "unregister", "-f", str(path), *common]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "unregistered": 2,
        "removed_inputs": 1,
    }
    assert cli.run(["dispatch", "register", "-f", str(path), *common]) == 0
    assert cli.run(["dispatch", "unregister", name, "--profile", name, *common]) == 0
    assert cli.run(["dispatch", "unregister", *common]) == 2
    assert cli.run(["dispatch", "unregister", name, "-f", str(path), *common]) == 2
    assert cli.run(["dispatch", "unregister", "--profile", name, *common]) == 0
    assert cli.run(["dispatch", "unregister", name, *common]) == 0


def test_management_requires_database(monkeypatch):
    monkeypatch.delenv("PROVIUM_DATABASE_URL", raising=False)
    for command in (["list"], ["queue"], ["unregister", "missing"]):
        assert cli.run(["dispatch", *command]) == 2
