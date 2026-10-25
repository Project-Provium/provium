import json
from pathlib import Path

import pytest
from pipeline_fixtures import COPY
from provium_text_pipeline_example.artifact import DOCUMENT
from provium_text_pipeline_example.procedure.tokenize.definition import TOKENIZE
from test_store import make_artifact

from provium_pipeline.models import PipelineConfiguration
from provium_pipeline.runtime import ExecutionContext, InputSet
from provium_pipeline.store import ArtifactStore, local_path


def graph():
    return PipelineConfiguration(
        name="chain",
        inputs={"document": DOCUMENT.identifier},
        nodes={
            "copy": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$inputs.document"},
            },
            "tokenize": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$nodes.copy.result"},
            },
        },
        outputs={"tokens": "$nodes.tokenize.result"},
    )


def execute(store, source, **options):
    steps = []
    with ExecutionContext(
        graph(),
        store,
        procedures={p.identifier: p for p in (COPY, TOKENIZE)},
        **options,
    ) as context:
        context.start(
            [InputSet({"document": str(source)})], on_step_finished=steps.append
        )
    return context, steps


@pytest.mark.parametrize("remote", [False, True])
def test_cached_chain_does_not_fetch_outputs_and_can_resume(
    tmp_path, monkeypatch, remote
):
    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "store")) as store:
        first, steps = execute(store, source)
        assert first.outcomes[0].state == "completed"
        copy_path = steps[0].outputs["result"].path
        locations = {}
        if remote:
            for manifest_path in (store.root / "results").glob("*.json"):
                manifest = json.loads(manifest_path.read_text())
                for metadata in manifest.values():
                    path = local_path(metadata["location"])
                    location = f"s3://bucket/{path.name}"
                    locations[location] = path
                    metadata["location"] = location
                manifest_path.write_text(json.dumps(manifest))
        fetched = []
        original = store.fetch

        def fetch(location):
            fetched.append(location)
            return locations[location] if location in locations else original(location)

        monkeypatch.setattr(store, "fetch", fetch)
        cached, steps = execute(store, source)
        assert cached.outcomes[0].state == "completed"
        assert all(step.reused for step in steps)
        assert fetched == [str(source)]
        assert cached.worker_starts == 0

        fetched.clear()
        resumed, steps = execute(store, source, force=["nodes.tokenize"])
        assert resumed.outcomes[0].state == "completed"
        assert steps[0].reused and not steps[1].reused
        assert resumed.worker_starts == 1
        assert any(
            locations.get(location, location) == Path(copy_path)
            or location == Path(copy_path).as_uri()
            for location in fetched
        )


def test_cached_repeated_and_setup_bindings_materialize_once(tmp_path, monkeypatch):
    from pipeline_fixtures import COLLECTION

    data = graph().model_dump()
    data["nodes"]["tokenize"] = {
        "procedure": COLLECTION.identifier,
        "inputs": {
            "sources": ["$nodes.copy.result", "$nodes.copy.result"],
            "optional": None,
        },
        "setup_inputs": {"optional": "$nodes.copy.result"},
    }
    pipeline = PipelineConfiguration.model_validate(data)
    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "store")) as store:
        procedures = {p.identifier: p for p in (COPY, COLLECTION)}
        for force in ([], ["nodes.tokenize"]):
            with ExecutionContext(
                pipeline, store, procedures=procedures, force=force
            ) as context:
                context.start([InputSet({"document": str(source)})])
            assert context.outcomes[0].state == "completed"
            if not force:
                fetched = []
                original = store.fetch

                def fetch(location):
                    fetched.append(location)
                    return original(location)

                monkeypatch.setattr(store, "fetch", fetch)
        assert context.worker_starts == 1
        assert len(fetched) == len(set(fetched))


def test_database_cached_manifests_need_no_output_reads(tmp_path, monkeypatch):
    from types import SimpleNamespace

    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "store")) as store:
        execute(store, source)
        manifest = store.manifest
        backend = SimpleNamespace(
            definition=lambda name: None,
            result=lambda namespace, key: manifest(key),
        )
        with ExecutionContext(
            graph(),
            store,
            coordinator=backend,
            procedures={COPY.identifier: COPY},
        ) as context:
            context._add(InputSet({"document": str(source)}))
            monkeypatch.setattr(
                store, "fetch", lambda location: pytest.fail("cached output fetched")
            )
            assert context._ready() == []
            assert context._states[0].steps == {"copy", "tokenize"}


def test_restore_rejects_invalid_metadata_without_fetching(tmp_path, monkeypatch):
    with ArtifactStore(str(tmp_path / "store")) as store:
        artifact = store.inspect(str(make_artifact(tmp_path / "source.pa")))
        expected = {"result": artifact.fingerprint["provenance"]}
        metadata = {
            "location": artifact.location,
            "identity": artifact.identity,
            "fingerprint": dict(artifact.fingerprint),
        }
        monkeypatch.setattr(
            store, "fetch", lambda location: pytest.fail("metadata caused fetch")
        )
        restored = store.restore({"result": metadata}, expected)["result"]
        assert restored.path is None
        metadata["fingerprint"]["content"] = "invalid"
        assert restored.fingerprint == artifact.fingerprint
        with pytest.raises(ValueError):
            store.restore({"result": metadata}, expected)
        with pytest.raises(ValueError, match="incomplete"):
            store.restore({}, expected)
        metadata["fingerprint"] = dict(artifact.fingerprint, provenance="a" * 64)
        with pytest.raises(ValueError, match="metadata"):
            store.restore({"result": metadata}, expected)


def test_cli_verification_flag(tmp_path, capsys):
    from provium.cli import run

    source = make_artifact(tmp_path / "source.pa")
    output_name = TOKENIZE.resolve_contract().metadata.outputs[0].name
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        "kind: PipelineConfiguration\nname: check\n"
        f"inputs: {{document: {DOCUMENT.identifier}}}\n"
        f"nodes:\n  tokenize:\n    procedure: {TOKENIZE.identifier}\n"
        "    inputs: {source: '$inputs.document'}\n"
        f"outputs: {{tokens: '$nodes.tokenize.{output_name}'}}\n"
    )
    args = [
        "pipeline",
        "run",
        "-f",
        str(pipeline),
        "--output",
        f"tokens={tmp_path / 'tokens.pa'}",
        "--input",
        f"document={source}",
        "--store",
        str(tmp_path / "store"),
        "--json",
    ]
    assert run(args) == 0
    capsys.readouterr()
    manifest = json.loads(
        next((tmp_path / "store" / "results").glob("*.json")).read_text()
    )
    local_path(manifest[output_name]["location"]).write_bytes(b"corrupt")
    assert run(args) == 0
    assert json.loads(capsys.readouterr().out)[0]["state"] == "completed"
    assert run([*args, "--verify-artifacts"]) == 1
    assert json.loads(capsys.readouterr().out)[0]["state"] == "failed"


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_verification_is_opt_in_and_execution_checks_cached_inputs(tmp_path, damage):
    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "store")) as store:
        _, steps = execute(store, source)
        path = Path(steps[0].outputs["result"].path)
        if damage == "missing":
            path.unlink()
        else:
            path.write_bytes(b"corrupt")
        cached, steps = execute(store, source)
        assert cached.outcomes[0].state == "completed"
        assert all(step.reused for step in steps)
        checked, _ = execute(store, source, verify_artifacts=True)
        assert checked.outcomes[0].state == "failed"
        resumed, _ = execute(store, source, force=["nodes.tokenize"])
        assert resumed.outcomes[0].state == "failed"
        assert resumed.worker_starts == 0
