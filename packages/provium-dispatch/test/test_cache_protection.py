import os
from uuid import uuid4

import psycopg
import pytest
from provium_pipeline.cache import clear_cache
from provium_pipeline.models import PipelineConfiguration
from provium_pipeline.store import ArtifactStore
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from test_cache_maintenance import cached
from test_store import make_artifact

from provium_dispatch.persistence.backends.postgres import PostgresBackend

pytestmark = pytest.mark.skipif(
    not os.environ.get("PROVIUM_TEST_DATABASE"), reason="requires PostgreSQL"
)


@pytest.fixture
def backend():
    url = os.environ["PROVIUM_TEST_DATABASE"]
    schema = "cache_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
        try:
            yield PostgresBackend(make_conninfo(url, options=f"-csearch_path={schema}"))
        finally:
            connection.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))


def test_maintenance_refuses_active_readers_and_forgets_results(tmp_path, backend):
    with ArtifactStore(str(tmp_path / "cache")) as store:
        outputs = cached(store, make_artifact(tmp_path / "source.pa"), "a" * 64)
        lease = backend.acquire(store.namespace, "a" * 64, uuid4().hex)
        assert backend.complete(lease, outputs)
        with backend.reader(store.namespace):
            with pytest.raises(RuntimeError, match="active"):
                clear_cache(store, guard=backend)
        assert store.manifest("a" * 64) == outputs
        assert clear_cache(store, guard=backend).removed == ("a" * 64,)
        assert backend.result(store.namespace, "a" * 64) is None


def test_enqueue_admission_and_live_lease_protect_cache(tmp_path, backend, monkeypatch):
    source = str(make_artifact(tmp_path / "source.pa"))
    graph = PipelineConfiguration(
        name="admission-" + uuid4().hex,
        inputs={"source": "provium_text_pipeline_example.DocumentV1"},
        nodes={},
        targets={"result": "$inputs.source"},
    )
    backend.register([graph.model_dump(mode="json")])
    with ArtifactStore(str(tmp_path / "cache")) as store:
        lease = backend.acquire(store.namespace, "e" * 64, uuid4().hex)
        with pytest.raises(RuntimeError, match="active computations"):
            clear_cache(store, guard=backend)
        backend.fail(lease, "finished")
        original = ArtifactStore.inspect

        def inspect(reader, location):
            with pytest.raises(RuntimeError, match="active work"):
                clear_cache(store, guard=backend)
            return original(reader, location)

        monkeypatch.setattr(ArtifactStore, "inspect", inspect)
        backend.enqueue(graph.name, {"source": source})


def test_dispatch_clear_command(tmp_path, backend):
    import argparse

    from provium_dispatch.cli.commands.cache import ClearCommand

    parser = argparse.ArgumentParser()
    command = ClearCommand()
    command.configure(parser)
    args = parser.parse_args(
        ["--database", backend.connection, "--store", str(tmp_path / "cache")]
    )
    assert command.execute(args) == 0


def test_queue_accepts_cache_without_local_root(tmp_path, backend):
    from contextlib import nullcontext

    from provium_dispatch import ExecutionContext

    source = str(make_artifact(tmp_path / "source.pa"))
    graph = PipelineConfiguration(
        name="external-" + uuid4().hex,
        inputs={"source": "provium_text_pipeline_example.DocumentV1"},
        nodes={},
        targets={"result": "$inputs.source"},
    )
    backend.register([graph.model_dump(mode="json")])
    backend.enqueue(graph.name, {"source": source})
    with ArtifactStore(str(tmp_path / "cache")) as files:

        class ExternalCache:
            root = None
            namespace = "external://" + uuid4().hex
            scratch = files.scratch
            inspect = files.inspect
            restore = files.restore

            def writer_lock(self, *, shared=False):
                return nullcontext()

        with ExecutionContext(graph, ExternalCache(), backend=backend) as execution:
            execution.start()
        assert execution.outcomes[0].state == "completed"


@pytest.mark.parametrize("group", ["pipeline", "dispatch"])
def test_cli_discards_after_publication(tmp_path, backend, group, monkeypatch):
    from provium.cli import run

    monkeypatch.chdir(tmp_path)
    source = str(make_artifact(tmp_path / "source.pa"))
    graph = PipelineConfiguration(
        name="discard-" + uuid4().hex,
        inputs={"source": "provium_text_pipeline_example.DocumentV1"},
        nodes={},
        targets={"result": "$inputs.source"},
    )
    destination = str(tmp_path / "result.pa")
    if group == "dispatch":
        backend.register([graph.model_dump(mode="json")])
        backend.enqueue(
            graph.name, {"source": source}, destinations={"result": destination}
        )
        options = [graph.name, "--database", backend.connection]
    else:
        path = tmp_path / "pipeline.json"
        path.write_text(graph.model_dump_json())
        options = [
            "-f",
            str(path),
            "--input",
            f"source={source}",
            "--output",
            f"result={destination}",
        ]
    assert (
        run(
            [
                group,
                "run",
                *options,
                "--store",
                str(tmp_path / "cache"),
                "--discard-cache",
            ]
        )
        == 0
    )
    assert (tmp_path / "result.pa").exists()


def test_pending_work_protects_cache_before_deletion(tmp_path, backend):
    source = str(make_artifact(tmp_path / "source.pa"))
    graph = PipelineConfiguration(
        name="protected-" + uuid4().hex,
        inputs={"source": "provium_text_pipeline_example.DocumentV1"},
        nodes={},
        targets={"result": "$inputs.source"},
    )
    backend.register([graph.model_dump(mode="json")])
    backend.enqueue(graph.name, {"source": source})
    with ArtifactStore(str(tmp_path / "cache")) as store:
        cached(store, source, "b" * 64)
        with pytest.raises(RuntimeError, match="queued"):
            clear_cache(store, guard=backend)
        assert store.manifest("b" * 64) is not None
