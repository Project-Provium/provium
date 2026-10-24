"""Queued scheduling preserves ordering, reuse, and unstarted work."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pipeline_fixtures import COPY
from test_cache_reads import graph
from test_store import make_artifact

from provium_pipeline.persistence.backends.postgres import PostgresBackend
from provium_pipeline.runtime import ExecutionContext
from provium_pipeline.store import ArtifactStore

DSN = os.environ.get("PROVIUM_TEST_DATABASE")
pytestmark = pytest.mark.skipif(not DSN, reason="requires PostgreSQL")


def queued(tmp_path, monkeypatch):
    import provium_pipeline.planning as planning

    monkeypatch.setattr(
        planning,
        "discover_procedure_catalogs",
        lambda: SimpleNamespace(resolve=lambda name: COPY),
    )
    pipeline = graph().model_copy(update={"name": "strategy-" + uuid4().hex})
    backend = PostgresBackend(DSN)
    backend.register([pipeline.model_dump(mode="json")])
    for index in range(2):
        source = make_artifact(tmp_path / f"{index}.pa", str(index))
        backend.enqueue(pipeline.name, {"document": str(source)})
    return pipeline, backend


@pytest.mark.parametrize(
    ("strategy", "order", "starts"),
    [
        ("item", ["copy", "tokenize", "copy", "tokenize"], 4),
        ("stage", ["copy", "copy", "tokenize", "tokenize"], 2),
    ],
)
def test_queued_order_and_cached_replay(tmp_path, monkeypatch, strategy, order, starts):
    pipeline, backend = queued(tmp_path, monkeypatch)
    events = []
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            pipeline,
            store,
            backend=backend,
            strategy=strategy,
            procedures={COPY.identifier: COPY},
        ) as execution:
            execution.queue_page_size = 1
            execution.start(on_step_finished=events.append)
        assert [event.node for event in events] == order
        assert execution.worker_starts == starts
        assert execution.max_resident_inputs == 1
        assert [event.state for event in execution.outcomes] == ["completed"] * 2
        for row in backend.pending(pipeline.name, include_finished=True):
            backend.enqueue(
                pipeline.name,
                {"document": row["inputs"]["document"]["location"]},
                queue_group="replay",
            )
        events.clear()
        with ExecutionContext(
            pipeline,
            store,
            backend=backend,
            strategy=strategy,
            queue_group="replay",
            procedures={COPY.identifier: COPY},
        ) as execution:
            execution.start(on_step_finished=events.append)
        assert execution.worker_starts == 0
        assert len(events) == 4
        assert all(event.reused for event in events)


@pytest.mark.parametrize("force", [False, True])
def test_item_failure_does_not_block_next_input(tmp_path, monkeypatch, force):
    pipeline, backend = queued(tmp_path, monkeypatch)
    (tmp_path / "0.pa").unlink()
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            pipeline,
            store,
            backend=backend,
            strategy="item",
            procedures={COPY.identifier: COPY},
        ) as execution:
            execution.start()
        assert [event.state for event in execution.outcomes] == ["failed", "completed"]
        with ExecutionContext(
            pipeline,
            store,
            backend=backend,
            strategy="item",
            procedures={COPY.identifier: COPY},
            force=["all"] if force else [],
        ) as replay:
            replay.start()
        assert replay.worker_starts == (2 if force else 0)
        assert len(replay.outcomes) == (2 if force else 0)


@pytest.mark.parametrize("cancel", [False, True])
def test_item_queue_waits_for_arrivals_and_can_cancel(tmp_path, monkeypatch, cancel):
    pipeline, backend = queued(tmp_path, monkeypatch)
    pending = backend.pending
    calls = 0

    def delayed(*args, **kwargs):
        nonlocal calls
        calls += 1
        return [] if calls == 1 or cancel else pending(*args, **kwargs)

    monkeypatch.setattr(backend, "pending", delayed)
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            pipeline,
            store,
            backend=backend,
            strategy="item",
            idle_wait=0.02,
            procedures={COPY.identifier: COPY},
        ) as execution:
            if cancel:
                monkeypatch.setattr(
                    execution._stop, "wait", lambda timeout: execution.cancel()
                )
            execution.start()
        assert len(execution.outcomes) == (0 if cancel else 2)
        assert calls >= (1 if cancel else 2)


def test_item_cancellation_preserves_unstarted_rows(tmp_path, monkeypatch):
    pipeline, backend = queued(tmp_path, monkeypatch)
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(
            pipeline,
            store,
            backend=backend,
            strategy="item",
            procedures={COPY.identifier: COPY},
        ) as execution:
            record = execution._record

            def stop_after_first(state, *, final=False):
                record(state, final=final)
                if final:
                    execution.cancel()

            monkeypatch.setattr(execution, "_record", stop_after_first)
            execution.start()
        assert len(execution.outcomes) == 1
        rows = backend.pending(pipeline.name, include_finished=True)
        assert [row["state"] for row in rows] == ["completed", "pending"]
