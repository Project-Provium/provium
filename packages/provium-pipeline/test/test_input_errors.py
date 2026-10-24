import json

import pytest
from provium.cli import run
from test_commands import config
from test_runtime_edges import context

from provium_pipeline.runtime import InputSet
from provium_pipeline.store import ArtifactStore


@pytest.mark.parametrize("bad_file", [False, True])
def test_known_input_errors_are_concise(tmp_path, capsys, bad_file):
    source = tmp_path / "invalid.pa"
    if bad_file:
        source.write_bytes(b"not an artifact")
    assert (
        run(
            [
                "pipeline",
                "run",
                "--output",
                f"tokens={tmp_path / 'tokens.pa'}",
                "-f",
                config(tmp_path),
                "--input",
                f"document={source}",
                "--store",
                str(tmp_path / "store"),
                "--json",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    outcome = json.loads(captured.out)[0]
    assert outcome["state"] == "failed"
    assert outcome["error"]
    assert str(source) in outcome["error"]
    assert "Traceback" not in captured.err
    assert "Traceback" not in outcome["error"]
    with ArtifactStore(str(tmp_path / "store")) as store, store.writer_lock():
        pass


def test_explicit_log_file_remains_supported(tmp_path, capsys):
    log = tmp_path / "run.log"
    with context(tmp_path, log_path=log) as execution:
        execution.start([InputSet({"document": str(tmp_path / "missing.pa")})])
    assert execution.log_path == log
    assert "file not found" in log.read_text()
    assert capsys.readouterr().err == ""
