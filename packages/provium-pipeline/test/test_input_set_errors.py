"""Validation and integrity checks for input-set lookup."""

from dataclasses import replace

import pytest
from test_runtime import configuration
from test_runtime_edges import context
from test_store import make_artifact

from provium_pipeline.input_sets import (
    inspect_inputs,
)
from provium_pipeline.lookup import lookup_artifact, split_selector
from provium_pipeline.planning import build_plan
from provium_pipeline.runtime import InputSet
from provium_pipeline.store import ArtifactStore


@pytest.mark.parametrize(
    "selector", ["plain", ".outputs.x", "p.nodes.x", "p.nodes..x", "p.outputs.x.y"]
)
def test_invalid_lookup_selector(selector):
    with pytest.raises(ValueError, match="expected pipeline"):
        split_selector(selector)


@pytest.mark.parametrize(
    ("selector", "mutation", "message"),
    [
        ("other.outputs.tokens", "none", "pipeline does not match"),
        ("tokenize.outputs.tokens", "names", "input names do not match"),
        ("tokenize.outputs.tokens", "type", "incompatible pipeline input"),
        ("tokenize.outputs.missing", "none", "unknown pipeline output"),
        ("tokenize.nodes.missing.result", "none", "unknown output reference"),
        ("tokenize.outputs.tokens", "reference", "unknown output reference"),
    ],
)
def test_lookup_rejects_incompatible_requests(tmp_path, selector, mutation, message):
    plan = build_plan(configuration())
    with ArtifactStore(str(tmp_path / "store")) as store:
        inputs = inspect_inputs(
            store, {"document": str(make_artifact(tmp_path / "source.pa"))}
        )
        if mutation == "names":
            inputs = {}
        elif mutation == "type":
            inputs["document"]["fingerprint"]["type"] = "wrong"
        elif mutation == "reference":
            plan.configuration.outputs["tokens"] = "$inputs.missing"
        with pytest.raises(ValueError, match=message):
            lookup_artifact(plan, store, inputs, selector)


def test_lookup_rejects_wrong_cached_output_type(tmp_path, monkeypatch):
    with context(tmp_path) as execution:
        source = make_artifact(tmp_path / "source.pa")
        execution.start([InputSet({"document": str(source)})])
        execution.close()
        assert execution.outcomes[0].state == "completed"
        store = execution.store
        inputs = inspect_inputs(store, {"document": str(source)})
        manifest = store.manifest

        def wrong_type(key):
            result = manifest(key)
            result["destination"]["fingerprint"]["type"] = "wrong"
            return result

        monkeypatch.setattr(store, "manifest", wrong_type)
        with pytest.raises(ValueError, match="incompatible cached output"):
            lookup_artifact(execution.plan, store, inputs, "tokenize.outputs.tokens")


@pytest.mark.parametrize(
    ("error", "message", "traceback"),
    [
        (IsADirectoryError("directory"), "expected a file, found a directory", False),
        (
            PermissionError("denied"),
            "permission denied; check file access permissions",
            False,
        ),
        (RuntimeError("unexpected failure"), "unexpected failure", True),
    ],
)
def test_input_access_errors_become_failed_outcomes(
    tmp_path, monkeypatch, capsys, error, message, traceback
):
    with context(tmp_path) as execution:

        def fail(location):
            raise error

        monkeypatch.setattr(execution.store, "inspect", fail)
        execution.start([InputSet({"document": str(tmp_path / "source.pa")})])
    outcome = execution.outcomes[0]
    assert outcome.state == "failed"
    assert message in outcome.error
    assert ("Traceback" in outcome.error) is traceback
    assert ("Traceback" in capsys.readouterr().err) is traceback


@pytest.mark.parametrize("field", ["identity", "fingerprint"])
def test_materialize_rejects_changed_artifacts(tmp_path, field):
    with ArtifactStore(str(tmp_path / "store")) as store:
        artifact = store.inspect(str(make_artifact(tmp_path / "source.pa")))
        changes = (
            {"identity": "different"}
            if field == "identity"
            else {"fingerprint": {**artifact.fingerprint, "content": "a" * 64}}
        )
        deferred = replace(artifact, path=None, **changes)
        with pytest.raises(ValueError, match="integrity/provenance mismatch"):
            store.materialize(deferred)
