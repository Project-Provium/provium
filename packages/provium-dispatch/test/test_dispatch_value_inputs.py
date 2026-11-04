"""Durable values use the same snapshots as local execution."""

import os
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from provium_dispatch.persistence.backends.postgres import PostgresBackend


def test_named_value_inputs_cli(tmp_path, capsys):
    from provium.cli import run

    flags = ["--store", str(tmp_path / "store")]
    values = ["--input", "count=3", "--input-type", "count=int"]
    assert run(["dispatch", "input-set", "register", "sample", *flags, *values]) == 0
    assert "count:" in capsys.readouterr().out
    assert run(["dispatch", "input-set", "resolve", "sample", *flags]) == 0
    assert "count:" in capsys.readouterr().out
    assert run(["dispatch", "input-set", "find", *flags, *values]) == 0
    assert "sample" in capsys.readouterr().out
    assert (
        run(
            [
                "dispatch",
                "input-set",
                "find",
                *flags,
                *values,
                "--input-type",
                "missing=float",
            ]
        )
        == 2
    )


def test_enqueue_persists_typed_values_without_artifact_reads(monkeypatch):
    from provium_pipeline.models import PipelineConfiguration

    graph = PipelineConfiguration(
        name="values", inputs={"count": {"value_type": "int"}}, nodes={}, outputs={}
    ).model_dump(mode="json")
    monkeypatch.setattr(PostgresBackend, "migrate", lambda self: None)
    backend = PostgresBackend("unused")
    monkeypatch.setattr(backend, "get", lambda *args: graph)
    monkeypatch.setattr(backend, "definition", lambda *args: None)
    captured = []

    class Connection:
        def execute(self, sql, args=None):
            if sql.startswith("SELECT r.document"):
                return SimpleNamespace(
                    fetchone=lambda: {"document": graph, "definition": None}
                )
            if sql.startswith("INSERT INTO provium_inputs"):
                captured.append(args[2].obj)
                return SimpleNamespace(fetchone=lambda: {"id": 42})

    @contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(backend, "connect", connect)
    assert backend.enqueue("values", {"count": 3}) == 42
    assert captured == [{"count": {"value_input": {"type": "int", "value": 3}}}]


@pytest.mark.skipif(
    not os.environ.get("PROVIUM_TEST_DATABASE"), reason="requires PostgreSQL"
)
def test_queued_value_execution(tmp_path, monkeypatch):
    import provium_pipeline.planning as planning
    from provium_pipeline.planning import build_plan
    from provium_pipeline.store import ArtifactStore
    from test_value_inputs import IMPORT, Labels, import_graph

    from provium_dispatch.runtime import ExecutionContext

    original = build_plan
    monkeypatch.setattr(
        planning,
        "build_plan",
        lambda configuration, definition=None, procedures=None: original(
            configuration, definition, {IMPORT.identifier: IMPORT}
        ),
    )
    graph = import_graph().model_copy(update={"name": "values-" + uuid4().hex})
    graph.inputs["labels"] = {"value_type": "pipeline-test-labels-v1"}
    graph.nodes["load"].inputs["labels"] = "$inputs.labels"
    backend = PostgresBackend(os.environ["PROVIUM_TEST_DATABASE"])
    document = graph.model_dump(mode="json")
    backend.register([document])
    store = ArtifactStore(str(tmp_path / "store"))
    try:
        values = {
            "source": "s3://bucket/data.csv",
            "count": 3,
            "scale": 2.5,
            "labels": Labels(("a", "b")),
        }
        identity = backend.enqueue(graph.name, values)
        assert backend.enqueue(graph.name, values) == identity
        assert backend.enqueue(graph.name, {**values, "count": 4}) != identity
        with ExecutionContext(
            graph, store, procedures={IMPORT.identifier: IMPORT}, backend=backend
        ) as run:
            run.start()
        assert len(run.outcomes) == 2
        assert all(event.state == "completed" for event in run.outcomes)
        assert run.worker_starts == 1
    finally:
        backend.unregister([document])
