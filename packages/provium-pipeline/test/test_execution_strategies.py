import pytest
from pipeline_fixtures import COPY
from test_cache_reads import graph
from test_store import make_artifact

from provium_pipeline.runtime import ExecutionContext, InputSet
from provium_pipeline.store import ArtifactStore


@pytest.mark.parametrize(
    ("strategy", "order", "starts"),
    [
        ("item", ["copy", "tokenize", "copy", "tokenize"], 4),
        ("stage", ["copy", "copy", "tokenize", "tokenize"], 2),
    ],
)
def test_strategy_controls_order_and_procedure_loading(
    tmp_path, strategy, order, starts
):
    items = [
        InputSet({"document": str(make_artifact(tmp_path / f"{i}.pa", str(i)))})
        for i in range(2)
    ]
    events = []
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}, strategy=strategy
        ) as execution:
            execution.start(items, on_step_finished=events.append)
        assert [event.node for event in events] == order
        assert execution.worker_starts == starts
        assert [event.state for event in execution.outcomes] == ["completed"] * 2
        events.clear()
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}, strategy=strategy
        ) as cached:
            cached.start(items, on_step_finished=events.append)
        assert cached.worker_starts == 0
        assert len(events) == 4
        assert all(event.reused for event in events)


def test_cli_accepts_execution_strategy():
    import argparse

    from provium_pipeline.cli.commands.run import RunCommand

    parser = argparse.ArgumentParser()
    RunCommand().configure(parser)
    for strategy in ("item", "stage"):
        assert parser.parse_args(["--strategy", strategy]).strategy == strategy


@pytest.mark.parametrize("failed", [False, True])
def test_finished_item_does_not_wait_for_idle_timeout(tmp_path, monkeypatch, failed):
    from provium_pipeline import runtime
    from provium_pipeline.runtime import State

    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}, idle_wait=60
        ) as execution:
            execution._states = [
                State(
                    InputSet({}),
                    values={"$nodes.tokenize.result": object()},
                    failed=failed,
                )
            ]
            monkeypatch.setattr(execution, "_ready", lambda: [])
            monkeypatch.setattr(execution, "_record", lambda *args, **kwargs: None)
            monkeypatch.setattr(
                runtime.time, "sleep", lambda delay: pytest.fail("finished item waited")
            )
            execution._run_local()


def test_strategy_rejects_unknown_value(tmp_path):
    with ArtifactStore(str(tmp_path / "store")) as store:
        with pytest.raises(ValueError, match="strategy"):
            ExecutionContext(graph(), store, strategy="unknown")
