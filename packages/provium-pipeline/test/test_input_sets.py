import pytest
from test_store import make_artifact

from provium_pipeline.input_sets import (
    input_set_hash,
    inspect_inputs,
)
from provium_pipeline.store import ArtifactStore


def test_direct_lookup_cli(tmp_path, monkeypatch, capsys):
    import json

    from provium.cli import run
    from test_commands import config

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
            ]
        )
        == 0
    )
    capsys.readouterr()
    lookup = ["pipeline", "lookup", "tokenize.outputs.tokens"]
    assert run([*lookup, "-f", pipeline, *inputs, *common, "--json"]) == 0
    artifact = json.loads(capsys.readouterr().out)["artifact"]
    assert run([*lookup, "-f", pipeline, *inputs, *common]) == 0
    assert capsys.readouterr().out.strip() == artifact["location"]
    assert run([*lookup, *inputs, *common]) == 2
    assert "provide -f" in capsys.readouterr().err
    assert (
        run([*lookup, "-f", pipeline, "--input", "document=missing.pa", *common]) == 2
    )
    assert "input 'document'" in capsys.readouterr().err


def test_input_fingerprint_validation(tmp_path):
    from provium_pipeline.input_sets import (
        assert_input_set_name_is_valid,
        assert_input_set_selector_is_valid,
    )

    with ArtifactStore(str(tmp_path / "store")) as store:
        with pytest.raises(ValueError, match="at least one named"):
            inspect_inputs(store, {})
    with pytest.raises(ValueError, match="at least one input"):
        input_set_hash({})
    with pytest.raises(ValueError, match="nonempty"):
        assert_input_set_name_is_valid(" bad")
    with pytest.raises(ValueError, match="name or hash"):
        assert_input_set_selector_is_valid(None, None)


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
