from provium_text_pipeline_example.artifact import DOCUMENT, TOKENS
from provium_text_pipeline_example.procedure.tokenize.definition import TOKENIZE
from test_store import make_artifact

from provium_pipeline.models import (
    ComputeProfile,
    PipelineConfiguration,
    PipelineDefinition,
)
from provium_pipeline.runtime import ExecutionContext, InputSet
from provium_pipeline.store import ArtifactStore


def configuration():
    return PipelineConfiguration(
        name="tokenize",
        nodes={
            "tokenize": {
                "procedure": TOKENIZE.identifier,
                "inputs": {"source": "$inputs.document"},
            }
        },
        outputs={"tokens": "$nodes.tokenize.destination"},
    )


def execute(tmp_path, inputs, **options):
    events = []
    with ExecutionContext(
        configuration(),
        ArtifactStore(str(tmp_path / "store")),
        definition=PipelineDefinition(
            inputs={"document": DOCUMENT},
            outputs={"tokens": TOKENS},
            profiles=frozenset({"local"}),
        ),
        **options,
    ) as context:
        context.start(
            inputs, on_step_finished=events.append, on_pipeline_finished=events.append
        )
    return context, events


def test_process_execution_cache_and_callbacks(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    first, events = execute(tmp_path, [item])
    assert first.finished
    assert first.error is None
    assert [event.state for event in events] == ["completed", "completed"]
    assert not events[0].reused
    second, reused = execute(tmp_path, [item])
    assert second.error is None
    assert reused[0].reused
    assert (
        reused[-1].outputs["tokens"].fingerprint
        == events[-1].outputs["tokens"].fingerprint
    )


def test_force_replaces_and_callbacks_still_complete(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    _, first = execute(tmp_path, [item])
    _, second = execute(tmp_path, [item], force=["all"])
    assert not second[0].reused
    assert first[-1].outputs["tokens"].location != second[-1].outputs["tokens"].location
    assert (
        first[-1].outputs["tokens"].fingerprint
        == second[-1].outputs["tokens"].fingerprint
    )


def test_multiple_inputs_reuse_resident_workers(tmp_path):
    items = [
        InputSet({"document": str(make_artifact(tmp_path / f"{n}.pa", str(n)))})
        for n in range(3)
    ]
    context, events = execute(tmp_path, items)
    assert context.error is None
    assert len(events) == 6
    assert context.worker_starts == 1


def test_callback_failure_does_not_reexecute(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    with ExecutionContext(
        configuration(), ArtifactStore(str(tmp_path / "store"))
    ) as context:
        context.start([item], on_step_finished=lambda _: 1 / 0)
    assert context.error is None
    assert context.outcomes[0].state == "completed"


def test_global_soft_limit_empty_and_partial_are_normal(tmp_path):
    profile = ComputeProfile(name="local", soft_time_limit=0.001)
    context, _ = execute(tmp_path, [], profile=profile)
    assert context.error is None


def test_equivalent_inputs_do_not_dispatch_twice(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    profile = ComputeProfile(name="local", workers=[{"default": 2}])
    context, events = execute(tmp_path, [item, item], profile=profile)
    assert context.error is None
    steps = [event for event in events if event.node is not None]
    assert sum(not event.reused for event in steps) == 1
    assert context.worker_starts == 1


def test_hard_limit_interrupts_process_and_context_survives(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    profile = ComputeProfile(name="local", hard_time_limit=0.02)
    context, events = execute(tmp_path, [item], profile=profile)
    assert context.error is None
    assert events[-1].state in ("failed", "partial")
    assert not events[-1].outputs


def test_callbacks_preserve_original_input_set(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    _, events = execute(tmp_path, [item])
    assert all(event.input_set is item for event in events)


def test_status_does_not_enqueue_work_and_closed_context_cannot_start(tmp_path):
    import pytest

    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    with ExecutionContext(
        configuration(), ArtifactStore(str(tmp_path / "store"))
    ) as context:
        assert context.inspect_inputs([item])[0]["state"] == "partial"
        context.start([])
    assert context.outcomes == []
    with pytest.raises(RuntimeError, match="closed"):
        other = ExecutionContext(
            configuration(), ArtifactStore(str(tmp_path / "other"))
        )
        other.close()
        other.start([])


def test_force_content_controls_descendant_reuse(tmp_path):
    from pipeline_fixtures import COPY, SOURCE

    for source_procedure, inputs, unchanged in (
        (COPY, {"document": str(make_artifact(tmp_path / "source.pa"))}, True),
        (SOURCE, {}, False),
    ):
        graph = PipelineConfiguration(
            name="chain",
            nodes={
                "source": {
                    "procedure": source_procedure.identifier,
                    "inputs": {"source": "$inputs.document"} if inputs else {},
                },
                "tokenize": {
                    "procedure": TOKENIZE.identifier,
                    "inputs": {"source": "$nodes.source.result"},
                },
            },
            outputs={"tokens": "$nodes.tokenize.destination"},
        )
        procedures = {p.identifier: p for p in (source_procedure, TOKENIZE)}
        store = ArtifactStore(str(tmp_path / source_procedure.identifier))
        events = []
        for force in ([], ["nodes.source"]):
            events.clear()
            with ExecutionContext(
                graph, store, procedures=procedures, force=force
            ) as context:
                context.start([InputSet(inputs)], on_step_finished=events.append)
            assert context.outcomes[0].state == "completed"
        assert not events[0].reused
        assert events[1].reused is unchanged


def test_failed_equivalent_inputs_do_not_retry_and_independent_work_continues(tmp_path):
    from pipeline_fixtures import COPY

    graph = PipelineConfiguration(
        name="copy",
        nodes={
            "copy": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$inputs.document"},
            }
        },
        outputs={"result": "$nodes.copy.result"},
    )
    bad = InputSet({"document": str(make_artifact(tmp_path / "bad.pa", "bad input"))})
    good = InputSet(
        {"document": str(make_artifact(tmp_path / "good.pa", "good input"))}
    )
    events = []
    with ExecutionContext(
        graph,
        ArtifactStore(str(tmp_path / "store")),
        procedures={COPY.identifier: COPY},
    ) as context:
        context.start([bad, bad, good], on_step_finished=events.append)
    assert [outcome.state for outcome in context.outcomes] == [
        "failed",
        "failed",
        "completed",
    ]
    assert len(events) == 3
    assert context.worker_starts == 1


def test_soft_expiry_drains_inflight_but_not_next_input(tmp_path):
    from pipeline_fixtures import COPY

    graph = PipelineConfiguration(
        name="copy",
        nodes={
            "copy": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$inputs.document"},
            }
        },
        outputs={"result": "$nodes.copy.result"},
    )
    inputs = [
        InputSet(
            {"document": str(make_artifact(tmp_path / f"{index}.pa", f"slow {index}"))}
        )
        for index in range(2)
    ]
    with ExecutionContext(
        graph,
        ArtifactStore(str(tmp_path / "store")),
        procedures={COPY.identifier: COPY},
        profile=ComputeProfile(name="test", soft_time_limit=0.3),
    ) as context:
        context.start(inputs)
    assert [outcome.state for outcome in context.outcomes] == ["completed", "partial"]


def test_branches_join_setup_binding_and_atomic_multiple_outputs(tmp_path):
    from pipeline_fixtures import COPY, JOIN

    graph = PipelineConfiguration(
        name="join",
        nodes={
            "left": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$inputs.left"},
            },
            "right": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$inputs.right"},
            },
            "join": {
                "procedure": JOIN.identifier,
                "inputs": {
                    "left": "$nodes.left.result",
                    "right": "$nodes.right.result",
                },
                "setup_inputs": {"weights": "$inputs.left"},
            },
        },
        outputs={"result": "$nodes.join.result", "mirror": "$nodes.join.mirror"},
    )
    item = InputSet(
        {
            "left": str(make_artifact(tmp_path / "left.pa", "left")),
            "right": str(make_artifact(tmp_path / "right.pa", "right")),
        }
    )
    events = []
    procedures = {p.identifier: p for p in (COPY, JOIN)}
    for iteration in range(2):
        events.clear()
        with ExecutionContext(
            graph, ArtifactStore(str(tmp_path / "store")), procedures=procedures
        ) as context:
            context.start([item], on_step_finished=events.append)
        assert context.outcomes[0].state == "completed"
        assert [event.node for event in events] == ["left", "right", "join"]
        outputs = context.outcomes[0].outputs
        assert (
            outputs["result"].fingerprint["content"]
            == outputs["mirror"].fingerprint["content"]
        )
        assert (
            outputs["result"].fingerprint["provenance"]
            != outputs["mirror"].fingerprint["provenance"]
        )
        assert all(event.reused == bool(iteration) for event in events)


def test_worker_exits_during_dispatch_is_an_item_failure(tmp_path, monkeypatch):
    from provium_pipeline.workers import Worker

    original = Worker.start

    class BrokenSend:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def send(self, value):
            raise BrokenPipeError("worker terminated during dispatch")

    def start(*args, **kwargs):
        worker = original(*args, **kwargs)
        worker.connection = BrokenSend(worker.connection)
        return worker

    monkeypatch.setattr(Worker, "start", start)
    item = InputSet({"document": str(make_artifact(tmp_path / "input.pa"))})
    context, _ = execute(tmp_path, [item])
    assert context.error is None
    assert context.outcomes[0].state == "failed"


def test_definition_is_snapshotted_before_execution(tmp_path):
    inputs = {"document": DOCUMENT}
    definition = PipelineDefinition(inputs, {"tokens": TOKENS})
    context = ExecutionContext(
        configuration(), ArtifactStore(str(tmp_path / "store")), definition=definition
    )
    inputs.clear()
    with context:
        context.start(
            [InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})]
        )
    assert context.outcomes[0].state == "completed"


def test_missing_initial_input_is_failed_before_loading(tmp_path):
    with ExecutionContext(
        configuration(), ArtifactStore(str(tmp_path / "store"))
    ) as context:
        context.start([InputSet({})])
    assert context.outcomes[0].state == "failed"
    assert context.worker_starts == 0


def test_idle_wait_rejects_nonfinite_values(tmp_path):
    import pytest

    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite"):
            ExecutionContext(
                configuration(), ArtifactStore(str(tmp_path / "store")), idle_wait=value
            )
