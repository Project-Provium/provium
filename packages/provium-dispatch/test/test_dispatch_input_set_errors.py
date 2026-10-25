"""Validation and integrity checks for input-set lookup."""

import json

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


def test_index_rejects_empty_inputs_and_remote_storage(tmp_path):
    with ArtifactStore(str(tmp_path / "store")) as store:
        with pytest.raises(ValueError, match="at least one named"):
            inspect_inputs(store, {})
    with pytest.raises(ValueError, match="at least one input"):
        input_set_hash({})
    with pytest.raises(ValueError, match="requires --database"):
        LocalInputSetIndex(None)


@pytest.mark.parametrize("name", ["", " leading", "trailing "])
def test_index_rejects_invalid_names(tmp_path, name):
    index = LocalInputSetIndex(tmp_path)
    with pytest.raises(ValueError, match="nonempty without surrounding whitespace"):
        index.register_input_set(name, {})
    assert index.find_input_sets("missing") == []


@pytest.mark.parametrize("selection", [{}, {"name": "video", "key": "hash"}])
@pytest.mark.parametrize("method", ["resolve_input_set", "unregister_input_sets"])
def test_index_requires_one_selector(tmp_path, selection, method):
    with pytest.raises(ValueError, match="by name or hash"):
        getattr(LocalInputSetIndex(tmp_path), method)(**selection)


def test_human_input_set_names(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    common = ["--store", str(tmp_path / "store")]
    inputs = ["--input", f"document={make_artifact(tmp_path / 'source.pa')}"]
    base = ["dispatch", "input-set"]
    assert run([*base, "find", *inputs, *common]) == 0
    assert capsys.readouterr().out == "No matching input-set names.\n"
    assert run([*base, "register", "video", *inputs, *common]) == 0
    capsys.readouterr()
    assert run([*base, "find", *inputs, *common]) == 0
    assert capsys.readouterr().out == "video\n"
    assert run([*base, "unregister", "video", *common]) == 0
    assert capsys.readouterr().out == "video\n"


def test_lookup_requires_pipeline_source(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert (
        run(["dispatch", "lookup", "tokenize.outputs.tokens", "--input-set", "video"])
        == 2
    )
    assert "provide -f" in capsys.readouterr().err


def test_lookup_rejects_corrupted_index(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "store"
    with ArtifactStore(str(root)) as store:
        inputs = inspect_inputs(
            store, {"document": str(make_artifact(tmp_path / "source.pa"))}
        )
        index = LocalInputSetIndex(store.root)
        record = index.register_input_set("video", inputs)
        inputs["document"]["fingerprint"]["content"] = "a" * 64
        with index.connect() as connection:
            connection.execute(
                "UPDATE input_sets SET inputs = ? WHERE hash = ?",
                (json.dumps(inputs), record["hash"]),
            )
    assert (
        run(
            [
                "dispatch",
                "lookup",
                "tokenize.outputs.tokens",
                "-f",
                config(tmp_path),
                "--input-set",
                "video",
                "--store",
                str(root),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "input-set index fingerprint mismatch" in captured.err
    assert "Traceback" not in captured.err
