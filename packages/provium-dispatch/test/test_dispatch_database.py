import os
from uuid import uuid4

import pytest

DSN = os.environ.get("PROVIUM_TEST_DATABASE")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set PROVIUM_TEST_DATABASE for PostgreSQL integration"
)


def test_registered_pipeline_lookup_uses_database_results(
    tmp_path, capsys, monkeypatch
):
    import json
    from pathlib import Path

    from provium.cli import run
    from provium_pipeline.store import ArtifactStore
    from test_commands import config
    from test_store import make_artifact

    monkeypatch.chdir(tmp_path)
    pipeline = config(tmp_path)
    name = "lookup-" + uuid4().hex
    Path(pipeline).write_text(
        Path(pipeline).read_text().replace("name: tokenize", f"name: {name}")
    )
    source = make_artifact(tmp_path / "source.pa")
    connection = ["--database", DSN]
    storage = ["--store", str(tmp_path / "store")]
    inputs = ["--input", f"document={source}"]
    assert run(["dispatch", "register", "-f", pipeline, *connection]) == 0
    capsys.readouterr()
    assert run(["dispatch", "run", name, *connection, *storage]) == 0
    assert capsys.readouterr().out == "No input sets processed.\n"
    assert run(["dispatch", "enqueue", name, *inputs, *connection]) == 0
    assert run(["dispatch", "run", name, *connection, *storage, "--json"]) == 0
    captured = capsys.readouterr()
    expected = json.loads(captured.out.splitlines()[-1])[0]["outputs"]["tokens"]
    assert not list((tmp_path / "store").glob("results/*.json"))
    assert (
        run(["dispatch", "input-set", "register", name, *inputs, *connection, *storage])
        == 0
    )
    capsys.readouterr()
    source.unlink()
    monkeypatch.setattr(
        ArtifactStore, "fetch", lambda *args: pytest.fail("lookup fetched artifact")
    )
    assert (
        run(
            [
                "dispatch",
                "lookup",
                f"{name}.outputs.tokens",
                "--input-set",
                name,
                *connection,
                *storage,
                "--json",
            ]
        )
        == 0
    )
    assert (
        json.loads(capsys.readouterr().out)["artifact"]["location"]
        == expected["location"]
    )
