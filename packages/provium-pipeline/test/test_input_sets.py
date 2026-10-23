import json
import shutil

import pytest
from provium.cli import run
from test_commands import config
from test_store import make_artifact

from provium_pipeline.input_sets import (
    LocalInputSetIndex,
    input_set_hash,
    inspect_inputs,
)
from provium_pipeline.store import ArtifactStore


def test_local_index_closes_connections(tmp_path, monkeypatch):
    import sqlite3

    import provium_pipeline.input_sets as module

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


def test_lookup_traverses_only_requested_dependencies(tmp_path, monkeypatch):
    from pipeline_fixtures import COLLECTION, COPY
    from test_cache_reads import graph

    from provium_pipeline.lookup import lookup_artifact
    from provium_pipeline.models import PipelineConfiguration
    from provium_pipeline.runtime import ExecutionContext, InputSet

    source = make_artifact(tmp_path / "source.pa")
    data = graph().model_dump()
    data["nodes"]["tokenize"] = {
        "procedure": COLLECTION.identifier,
        "inputs": {"sources": ["$nodes.copy.result", "$nodes.copy.result"]},
        "setup_inputs": {"optional": "$nodes.copy.result"},
    }
    pipeline = PipelineConfiguration.model_validate(data)
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            pipeline, store, procedures={p.identifier: p for p in (COPY, COLLECTION)}
        ) as context:
            context.start([InputSet({"document": str(source)})])
        assert context.outcomes[0].state == "completed"
        inputs = inspect_inputs(store, {"document": str(source)})
        plan = context.plan
        monkeypatch.setattr(
            store, "fetch", lambda *args: pytest.fail("lookup fetched artifact")
        )
        result = lookup_artifact(plan, store, inputs, "chain.outputs.tokens")
        assert result.location == context.outcomes[0].outputs["tokens"].location
        original = store.manifest
        first_key = next(
            path.stem
            for path in (store.root / "results").glob("*.json")
            if original(path.stem)["result"]["location"]
            == lookup_artifact(plan, store, inputs, "chain.nodes.copy.result").location
        )
        monkeypatch.setattr(
            store, "manifest", lambda key: original(key) if key == first_key else None
        )
        assert lookup_artifact(plan, store, inputs, "chain.nodes.copy.result")
        with pytest.raises(ValueError, match="no cached result.*tokenize"):
            lookup_artifact(plan, store, inputs, "chain.outputs.tokens")


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
    base = ["pipeline", "input-set"]
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
    assert run(["pipeline", "run", "-f", pipeline, *inputs, *common, "--json"]) == 0
    expected = json.loads(capsys.readouterr().out)[0]["outputs"]["tokens"]
    assert (
        run(["pipeline", "input-set", "register", "video", *inputs, *common, "--json"])
        == 0
    )
    key = json.loads(capsys.readouterr().out)["hash"]
    lookup = ["pipeline", "lookup", "tokenize.outputs.tokens", "-f", pipeline, *common]
    assert run([*lookup, *inputs, "--json"]) == 0
    assert (
        json.loads(capsys.readouterr().out)["artifact"]["location"]
        == expected["location"]
    )
    source.unlink()
    monkeypatch.setattr(
        ArtifactStore, "fetch", lambda *a: pytest.fail("lookup fetched artifact")
    )
    for selector in ["tokenize.outputs.tokens", "tokenize.nodes.tokenize.destination"]:
        for selection in [["--input-set", "video"], ["--input-set-hash", key]]:
            assert (
                run(
                    [
                        "pipeline",
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
                "pipeline",
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
                "pipeline",
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
    assert "provium pipeline lookup:" in captured.err
    assert "Traceback" not in captured.err


def test_lookup_rejects_ambiguous_input_selection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        run(
            [
                "pipeline",
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
        run(["pipeline", "input-set", "unregister", "name", "--input", "x=file.pa"])
        == 2
    )


def test_hash_excludes_identities_and_binding_order_but_preserves_fingerprints():
    a = {
        "identity": "first",
        "location": "first.pa",
        "fingerprint": {"type": "A", "provenance": "a" * 64, "content": "b" * 64},
    }
    b = {
        "identity": "second",
        "location": "second.pa",
        "fingerprint": {"type": "B", "provenance": "c" * 64, "content": "d" * 64},
    }
    key = input_set_hash({"a": a, "b": b})
    assert (
        input_set_hash({"b": b, "a": {**a, "identity": "other", "location": "copy.pa"}})
        == key
    )
    for field in ("type", "provenance", "content"):
        changed = {**a, "fingerprint": {**a["fingerprint"], field: "e" * 64}}
        assert input_set_hash({"a": changed, "b": b}) != key


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


def test_lookup_handles_a_long_pipeline_without_recursion(tmp_path, monkeypatch):
    from pipeline_fixtures import COPY
    from provium.semantic import computation_key, output_key

    from provium_pipeline.lookup import lookup_artifact
    from provium_pipeline.models import PipelineConfiguration
    from provium_pipeline.planning import build_plan

    with ArtifactStore(str(tmp_path / "store")) as store:
        inputs = inspect_inputs(
            store, {"document": str(make_artifact(tmp_path / "source.pa"))}
        )
        nodes = {}
        source = "$inputs.document"
        for number in range(700):
            name = f"step{number}"
            nodes[name] = {"procedure": COPY.identifier, "inputs": {"source": source}}
            source = f"$nodes.{name}.result"
        plan = build_plan(
            PipelineConfiguration(
                name="long",
                inputs={"document": inputs["document"]["fingerprint"]["type"]},
                nodes=nodes,
                outputs={"final": source},
            ),
            procedures={COPY.identifier: COPY},
        )
        previous = inputs["document"]["fingerprint"]
        manifests = {}
        for name in plan.order:
            key = computation_key(plan.records[name], {"source": previous}, {})
            previous = {
                **previous,
                "provenance": output_key(key, "result", previous["type"]),
            }
            manifests[key] = {
                "result": {
                    "location": f"file:///unused/{name}.pa",
                    "identity": name,
                    "fingerprint": previous,
                }
            }
        monkeypatch.setattr(store, "manifest", manifests.get)
        monkeypatch.setattr(
            store, "fetch", lambda *args: pytest.fail("artifact fetched")
        )
        assert lookup_artifact(
            plan, store, inputs, "long.outputs.final"
        ).location.endswith("step699.pa")
