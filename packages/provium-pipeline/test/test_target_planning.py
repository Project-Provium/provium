import pytest
from pipeline_fixtures import COPY
from provium_text_pipeline_example.artifact import DOCUMENT
from test_store import make_artifact

from provium_pipeline.models import PipelineConfiguration
from provium_pipeline.runtime import ExecutionContext, InputSet
from provium_pipeline.store import ArtifactStore


def graph():
    return PipelineConfiguration(
        name="branches",
        inputs={"left": DOCUMENT.identifier, "right": DOCUMENT.identifier},
        nodes={
            "left": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$inputs.left"},
            },
            "right": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$inputs.right"},
            },
            "last": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$nodes.left.result"},
            },
        },
        targets={
            "left": "$nodes.left.result",
            "right": "$nodes.right.result",
            "last": "$nodes.last.result",
        },
    )


@pytest.mark.parametrize("strategy", ["item", "stage"])
@pytest.mark.parametrize("target", ["left", "nodes.left.result"])
def test_selected_output_limits_execution_and_required_inputs(
    tmp_path, strategy, target
):
    events = []
    pipeline = graph()
    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            pipeline,
            store,
            procedures={COPY.identifier: COPY},
            targets=[target],
            strategy=strategy,
        ) as execution:
            execution.start(
                [InputSet({"left": str(source)})], on_step_finished=events.append
            )
        assert execution.plan.order == ["left"]
        assert execution.plan.input_types == {"left": DOCUMENT.identifier}
        assert [event.node for event in events] == ["left"]
        assert execution.outcomes[0].state == "completed"
        assert set(execution.outcomes[0].outputs) == {target}
    assert set(pipeline.inputs) == {"left", "right"}
    assert set(pipeline.nodes) == {"left", "right", "last"}


@pytest.mark.parametrize("targets", [[], ["missing"], ["nodes.left.missing"]])
def test_invalid_target_selection_fails_before_execution(tmp_path, targets):
    with ArtifactStore(str(tmp_path / "store")) as store:
        with pytest.raises(ValueError, match="target|output"):
            ExecutionContext(
                graph(), store, procedures={COPY.identifier: COPY}, targets=targets
            )


def test_dependency_closure_includes_setup_and_repeated_inputs():
    from pipeline_fixtures import COLLECTION

    from provium_pipeline.planning import build_plan, select_targets

    data = graph().model_dump()
    data["nodes"]["join"] = {
        "procedure": COLLECTION.identifier,
        "inputs": {"sources": ["$nodes.left.result", "$nodes.left.result"]},
        "setup_inputs": {"optional": "$inputs.right"},
    }
    data["outputs"]["joined"] = "$nodes.join.result"
    plan = build_plan(
        PipelineConfiguration.model_validate(data),
        procedures={p.identifier: p for p in (COPY, COLLECTION)},
    )
    selected = select_targets(plan, ["joined", "left"])
    assert selected.order == ["left", "join"]
    assert set(selected.input_types) == {"left", "right"}
    assert set(selected.configuration.outputs) == {"joined", "left"}


def test_pass_through_target_needs_no_procedures(tmp_path):
    pipeline = PipelineConfiguration(
        name="pass",
        inputs={"source": DOCUMENT.identifier},
        nodes={},
        targets={"original": "$inputs.source"},
    )
    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(pipeline, store, targets=["original"]) as execution:
            execution.start([InputSet({"source": str(source)})])
        assert execution.worker_starts == 0
        assert execution.outcomes[0].state == "completed"


def test_selected_target_reuses_full_pipeline_cache(tmp_path):
    inputs = {
        name: str(make_artifact(tmp_path / f"{name}.pa", name))
        for name in ("left", "right")
    }
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}
        ) as full:
            full.start([InputSet(inputs)])
        events = []
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}, targets=["last"]
        ) as selected:
            selected.start(
                [InputSet({"left": inputs["left"]})], on_step_finished=events.append
            )
        assert selected.worker_starts == 0
        assert all(event.reused for event in events)
        assert len(events) == 2
        actual = selected.outcomes[0].outputs["last"]
        expected = full.outcomes[0].outputs["last"]
        assert actual.location == expected.location
        assert actual.fingerprint == expected.fingerprint


def test_selected_target_still_requires_its_inputs(tmp_path):
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}, targets=["left"]
        ) as execution:
            execution.start([InputSet({})])
        assert execution.outcomes[0].state == "failed"
        assert "missing: left" in execution.outcomes[0].error
        assert execution.worker_starts == 0


def test_target_selection_rejects_legacy_queue_backend(tmp_path):
    from types import SimpleNamespace

    with ArtifactStore(str(tmp_path / "store")) as store:
        with pytest.raises(ValueError, match="target selection"):
            ExecutionContext(
                graph(),
                store,
                backend=SimpleNamespace(definition=lambda name: None),
                targets=["left"],
            )


def test_targets_accept_legacy_outputs_but_reject_both():
    data = graph().model_dump()
    assert PipelineConfiguration.model_validate(data).targets == graph().targets
    with pytest.raises(ValueError):
        PipelineConfiguration.model_validate({**data, "targets": graph().targets})
