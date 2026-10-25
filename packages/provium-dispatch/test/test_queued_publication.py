import argparse
import os
from uuid import uuid4

import pytest
from pipeline_fixtures import COPY, DOCUMENT
from provium_pipeline import ArtifactStore, PipelineConfiguration
from test_store import make_artifact

from provium_dispatch import ExecutionContext
from provium_dispatch.cli.commands.enqueue import EnqueueCommand
from provium_dispatch.persistence.backends.postgres import PostgresBackend

pytestmark = pytest.mark.skipif(
    not os.environ.get("PROVIUM_TEST_DATABASE"), reason="requires PostgreSQL"
)


@pytest.mark.parametrize("strategy", ["item", "stage"])
def test_queued_destinations_are_pinned_and_share_computation(
    tmp_path, strategy, monkeypatch
):
    monkeypatch.setattr(
        "provium.procedure.catalog.ProcedureCatalog.resolve", lambda self, name: COPY
    )
    backend = PostgresBackend(os.environ["PROVIUM_TEST_DATABASE"])
    graph = PipelineConfiguration(
        name="publication-" + uuid4().hex,
        inputs={"source": DOCUMENT.identifier},
        nodes={
            "copy": {
                "procedure": COPY.identifier,
                "inputs": {"source": "$inputs.source"},
            }
        },
        targets={"result": "$nodes.copy.result"},
    )
    backend.register([graph.model_dump(mode="json")])
    source = str(make_artifact(tmp_path / "source.pa"))
    first = {"result": str(tmp_path / "first.pa")}
    second = {"result": str(tmp_path / "second.pa")}
    identity = backend.enqueue(graph.name, {"source": source}, destinations=first)
    assert (
        backend.enqueue(graph.name, {"source": source}, destinations=first) == identity
    )
    assert (
        backend.enqueue(graph.name, {"source": source}, destinations=second) != identity
    )
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with ExecutionContext(
            graph,
            store,
            backend=backend,
            procedures={COPY.identifier: COPY},
            strategy=strategy,
        ) as execution:
            execution.start()
        assert execution.error is None
        assert [outcome.state for outcome in execution.outcomes] == [
            "completed",
            "completed",
        ]
        assert (
            store.inspect(first["result"]).fingerprint
            == store.inspect(second["result"]).fingerprint
        )
        assert not backend.pending(graph.name)
        with ExecutionContext(
            graph,
            store,
            backend=backend,
            procedures={COPY.identifier: COPY},
            strategy=strategy,
            force=["all"],
        ) as forced:
            forced.start()
        assert [outcome.state for outcome in forced.outcomes] == [
            "completed",
            "completed",
        ]
        for row in backend.pending(graph.name, include_finished=True):
            assert row["detail"]["outputs"]["result"] in {
                store.inspect(first["result"]).location,
                store.inspect(second["result"]).location,
            }


def test_enqueue_csv_paths_and_blank_outputs(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backend = PostgresBackend(os.environ["PROVIUM_TEST_DATABASE"])
    graph = PipelineConfiguration(
        name="csv-" + uuid4().hex,
        inputs={"source": DOCUMENT.identifier, "unused": DOCUMENT.identifier},
        nodes={},
        targets={"first": "$inputs.source", "second": "$inputs.source"},
    )
    backend.register([graph.model_dump(mode="json")])
    source = make_artifact(tmp_path / "source.pa")
    batch = tmp_path / "batch.csv"
    batch.write_text(f"inputs.source,first,second\n{source},{tmp_path / 'first.pa'},\n")
    parser = argparse.ArgumentParser()
    command = EnqueueCommand()
    command.configure(parser)
    args = parser.parse_args(
        [
            graph.name,
            "--database",
            os.environ["PROVIUM_TEST_DATABASE"],
            "--csv",
            str(batch),
        ]
    )
    assert command.execute(args) == 0
    assert len(backend.pending(graph.name)) == 1
    assert backend.pending(graph.name)[0]["destinations"] == {
        "first": str(tmp_path / "first.pa")
    }
    direct = parser.parse_args(
        [
            graph.name,
            "--database",
            os.environ["PROVIUM_TEST_DATABASE"],
            "--input",
            f"source={source}",
            "--output",
            "second=second.pa",
        ]
    )
    assert command.execute(direct) == 0
    assert len(backend.pending(graph.name)) == 2
    assert backend.pending(graph.name)[1]["destinations"]["second"] == str(
        tmp_path / "second.pa"
    )
    backend.enqueue(
        graph.name,
        {"source": str(source)},
        destinations={"first": "s3://example/results/output.pa"},
    )
    assert backend.pending(graph.name)[2]["destinations"]["first"].startswith("s3://")
    args.input = [f"source={source}"]
    assert command.execute(args) == 2
    assert "--csv cannot" in capsys.readouterr().err
