import json
import shutil

import pytest
from provium.cli import run
from provium_pipeline.input_sets import (
    input_set_hash,
    inspect_inputs,
)
from provium_pipeline.store import ArtifactStore
from test_commands import config
from test_store import make_artifact

from provium_dispatch.input_sets import LocalInputSetIndex


def test_local_index_closes_connections(tmp_path, monkeypatch):
    import sqlite3

    import provium_dispatch.input_sets as module

    connections = []
    connect = sqlite3.connect

    def tracked(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", tracked)
    index = LocalInputSetIndex(tmp_path)
    index.find_input_sets("missing")
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_input_set_identity_and_alias_lifecycle(tmp_path):
    source = make_artifact(tmp_path / "source.pa")
    copied = tmp_path / "renamed.pa"
    shutil.copyfile(source, copied)
    with ArtifactStore(str(tmp_path / "store")) as store:
        inputs = inspect_inputs(store, {"document": str(source)})
        renamed = inspect_inputs(store, {"document": str(copied)})
        key = input_set_hash(inputs)
        assert input_set_hash(renamed) == key
        assert input_set_hash({"other": inputs["document"]}) != key
        other = inspect_inputs(
            store, {"document": str(make_artifact(tmp_path / "other.pa", "different"))}
        )
        index = LocalInputSetIndex(store.root)
        assert index.register_input_set("video", inputs)["hash"] == key
        assert index.register_input_set("video", renamed)["hash"] == key
        index.register_input_set("alias", inputs)
        with pytest.raises(ValueError, match="already"):
            index.register_input_set("video", other)
        assert index.find_input_sets(key) == ["alias", "video"]
        assert index.resolve_input_set(name="video")["hash"] == key
        assert index.unregister_input_sets(name="video") == ["video"]
        assert index.find_input_sets(key) == ["alias"]
        assert index.unregister_input_sets(key=key) == ["alias"]
        assert index.resolve_input_set(key=key)["hash"] == key
        assert (
            LocalInputSetIndex(store.root).resolve_input_set(key=key)["inputs"]
            == inputs
        )
        with pytest.raises(ValueError, match="unknown"):
            index.resolve_input_set(name="video")


def test_cli_register_resolve_find_unregister(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = make_artifact(tmp_path / "source.pa")
    common = ["--store", str(tmp_path / "store"), "--json"]
    inputs = ["--input", f"document={source}"]
    base = ["dispatch", "input-set"]
    assert run([*base, "register", "video", *inputs, *common]) == 0
    key = json.loads(capsys.readouterr().out)["hash"]
    assert run([*base, "resolve", "video", *common]) == 0
    assert json.loads(capsys.readouterr().out)["hash"] == key
    assert run([*base, "find", *inputs, *common]) == 0
    assert json.loads(capsys.readouterr().out) == ["video"]
    assert run([*base, "unregister", *inputs, *common]) == 0
    assert json.loads(capsys.readouterr().out) == ["video"]
    assert source.exists()
    assert run([*base, "resolve", "video", *common]) == 2
    assert "unknown" in capsys.readouterr().err


def test_lookup_named_inputs_needs_no_artifact_reads(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = make_artifact(tmp_path / "source.pa")
    pipeline = config(tmp_path)
    common = ["--store", str(tmp_path / "store")]
    inputs = ["--input", f"document={source}"]
    assert (
        run(
            [
                "pipeline",
                "run",
                "-f",
                pipeline,
                *inputs,
                *common,
                "--output",
                f"tokens={tmp_path / 'tokens.pa'}",
                "--json",
            ]
        )
        == 0
    )
    expected = json.loads(capsys.readouterr().out)[0]["outputs"]["tokens"]
    assert (
        run(["dispatch", "input-set", "register", "video", *inputs, *common, "--json"])
        == 0
    )
    key = json.loads(capsys.readouterr().out)["hash"]
    lookup = ["dispatch", "lookup", "tokenize.outputs.tokens", "-f", pipeline, *common]
    assert run([*lookup, *inputs, "--json"]) == 0
    cached = json.loads(capsys.readouterr().out)["artifact"]
    assert cached["content"] == expected["content"]
    assert cached["provenance"] == expected["provenance"]
    expected = cached
    source.unlink()
    monkeypatch.setattr(
        ArtifactStore, "fetch", lambda *a: pytest.fail("lookup fetched artifact")
    )
    for selector in ["tokenize.outputs.tokens", "tokenize.nodes.tokenize.destination"]:
        for selection in [["--input-set", "video"], ["--input-set-hash", key]]:
            assert (
                run(
                    [
                        "dispatch",
                        "lookup",
                        selector,
                        "-f",
                        pipeline,
                        *common,
                        *selection,
                        "--json",
                    ]
                )
                == 0
            )
            result = json.loads(capsys.readouterr().out)
            assert result["input_set_hash"] == key
            assert result["artifact"]["location"] == expected["location"]
    assert run([*lookup, "--input-set", "video"]) == 0
    assert capsys.readouterr().out.strip() == expected["location"]
    assert (
        run(
            [
                "dispatch",
                "lookup",
                "tokenize.nodes.missing.result",
                "-f",
                pipeline,
                *common,
                "--input-set",
                "video",
            ]
        )
        == 2
    )
    assert "unknown" in capsys.readouterr().err


@pytest.mark.parametrize(
    "selection",
    [
        ["--input", "document=missing.pa"],
        ["--input", "document"],
        ["--input", "document=a", "--input", "document=b"],
        ["--input-set", "missing"],
    ],
)
def test_lookup_input_errors_are_concise(tmp_path, capsys, monkeypatch, selection):
    monkeypatch.chdir(tmp_path)
    assert (
        run(
            [
                "dispatch",
                "lookup",
                "tokenize.outputs.tokens",
                "-f",
                config(tmp_path),
                "--store",
                str(tmp_path / "store"),
                *selection,
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "provium dispatch lookup:" in captured.err
    assert "Traceback" not in captured.err


def test_lookup_rejects_ambiguous_input_selection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        run(
            [
                "dispatch",
                "lookup",
                "p.outputs.x",
                "--input",
                "x=file.pa",
                "--input-set",
                "name",
            ]
        )
    assert error.value.code == 2
    assert (
        run(["dispatch", "input-set", "unregister", "name", "--input", "x=file.pa"])
        == 2
    )


def test_local_registration_conflicts_are_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    index = LocalInputSetIndex(tmp_path)
    inputs = [
        {
            "x": {
                "fingerprint": {
                    "type": "A",
                    "provenance": "a" * 64,
                    "content": char * 64,
                }
            }
        }
        for char in "bc"
    ]

    def register(value):
        try:
            return index.register_input_set("shared", value)["hash"]
        except ValueError:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(register, inputs))
    successful = [key for key in results if key is not None]
    assert len(successful) == 1
    assert index.resolve_input_set(name="shared")["hash"] == successful[0]
    losing = input_set_hash(inputs[results.index(None)])
    with pytest.raises(ValueError, match="unknown"):
        index.resolve_input_set(key=losing)
