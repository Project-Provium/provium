"""Queue isolation, default selection, and upgrades."""

import argparse
import os
from uuid import uuid4

import pytest
from provium import cli

from provium_pipeline.persistence.backends.postgres import PostgresBackend


@pytest.fixture
def db():
    dsn = os.environ.get("PROVIUM_TEST_DATABASE")
    if not dsn:
        pytest.skip("requires PostgreSQL")
    return PostgresBackend(dsn)


def document(name):
    return {
        "kind": "PipelineConfiguration",
        "name": name,
        "inputs": {},
        "nodes": {},
        "outputs": {},
    }


def test_groups_isolate_deduplication_reads_and_retry(db):
    name = "queues-" + uuid4().hex
    db.register([document(name)])
    default = db.enqueue(name, {})
    priority = db.enqueue(name, {}, queue_group="priority")
    assert default != priority
    assert db.enqueue(name, {}, queue_group="priority") == priority
    assert [row["id"] for row in db.pending(name)] == [default]
    assert [row["id"] for row in db.pending(name, queue_group="priority")] == [priority]
    db.progress(default, "failed", {})
    db.progress(priority, "failed", {})
    assert db.retry(name, queue_group="priority") == 1
    assert db.pending(name) == []
    assert db.queue_info(name, queue_group="priority")[0]["states"]["pending"] == 1
    assert db.queue_info(name)[0]["states"]["failed"] == 1
    assert db.queue_info(name, queue_group="empty")[0]["total"] == 0
    assert db.unregister([document(name)])["removed_inputs"] == 2


def test_cli_group_selection_and_status(db, tmp_path, capsys):
    name = "queues-" + uuid4().hex
    db.register([document(name)])
    common = ["--database", db.connection]
    assert cli.run(["dispatch", "enqueue", name, "-q", "priority", *common]) == 0
    assert db.pending(name) == []
    priority = db.pending(name, queue_group="priority")[0]["id"]
    db.enqueue(name, {})
    capsys.readouterr()
    assert (
        cli.run(
            [
                "pipeline",
                "status",
                name,
                "--queue",
                "priority",
                *common,
                "--store",
                str(tmp_path / "artifacts"),
            ]
        )
        == 0
    )
    import json

    assert [row["id"] for row in json.loads(capsys.readouterr().out)] == [priority]
    assert (
        cli.run(
            [
                "pipeline",
                "run",
                name,
                "-q",
                "priority",
                *common,
                "--store",
                str(tmp_path / "artifacts"),
            ]
        )
        == 0
    )
    assert db.pending(name)
    assert db.pending(name, queue_group="priority") == []
    capsys.readouterr()
    assert (
        cli.run(
            [
                "pipeline",
                "status",
                name,
                "-q",
                "priority",
                "--outputs",
                *common,
                "--store",
                str(tmp_path / "artifacts"),
            ]
        )
        == 0
    )
    assert [row["id"] for row in json.loads(capsys.readouterr().out)] == [priority]
    assert cli.run(["dispatch", "queue", name, "-q", "priority", *common]) == 0
    summary = json.loads(capsys.readouterr().out)[0]
    assert summary["queue"] == "priority"
    assert summary["states"]["completed"] == 1
    db.progress(priority, "completed", {})
    with db.connect() as connection:
        connection.execute(
            "UPDATE provium_inputs SET state='failed' WHERE id=%s", (priority,)
        )
    assert cli.run(["dispatch", "retry", name, "-q", "priority", *common]) == 0
    assert json.loads(capsys.readouterr().out) == {"reset": 1}


def test_default_selection_configuration(tmp_path, monkeypatch):
    from provium_pipeline.cli.arguments import configure_queue, require_queue

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    parser = argparse.ArgumentParser()
    configure_queue(parser)
    args = parser.parse_args([])
    require_queue(args)
    assert args.queue == "default"
    directory = tmp_path / ".provium"
    directory.mkdir()
    path = directory / "config.toml"
    path.write_text('[pipeline.queue]\ndefault_group = "batch"\n')
    args = parser.parse_args([])
    require_queue(args)
    assert args.queue == "batch"
    path.write_text("[pipeline.queue]\ndefault_enabled = false\n")
    with pytest.raises(ValueError, match="--queue"):
        require_queue(parser.parse_args([]))
    args = parser.parse_args(["-q", "priority"])
    require_queue(args)
    assert args.queue == "priority"


@pytest.mark.parametrize("value", ["", " ", " bad", "bad ", 1])
def test_invalid_queue_names(db, value):
    with pytest.raises((ValueError, TypeError), match="queue"):
        db.enqueue("unused", {}, queue_group=value)


@pytest.mark.parametrize(
    "text",
    [
        "queue = 1",
        '[pipeline.queue]\ndefault_enabled = "no"',
        '[pipeline.queue]\ndefault_group = ""',
    ],
)
def test_invalid_queue_configuration(tmp_path, monkeypatch, text):
    from provium_pipeline.cli.arguments import configure_queue, require_queue

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".provium"
    directory.mkdir()
    (directory / "config.toml").write_text("[pipeline]\n" + text)
    parser = argparse.ArgumentParser()
    configure_queue(parser)
    with pytest.raises((ValueError, TypeError), match="queue"):
        require_queue(parser.parse_args([]))


def test_upgrade_preserves_existing_queue_and_dedup(db, monkeypatch):
    import psycopg
    from provium.semantic import canonical_hash
    from psycopg import sql
    from psycopg.types.json import Jsonb

    import provium_pipeline.persistence.backends.postgres as module

    schema = "queue_upgrade_" + uuid4().hex
    with db.connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    dsn = psycopg.conninfo.make_conninfo(
        db.connection, options=f"-c search_path={schema}"
    )
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "MIGRATIONS", module.MIGRATIONS[:2])
            old = PostgresBackend(dsn)
            old.register([document("existing")])
            snapshot = old.get("PipelineConfiguration", "existing")
            dedup = canonical_hash(
                {"pipeline": snapshot, "definition": None, "inputs": {}}
            )
            with old.connect() as connection:
                identity = connection.execute(
                    "INSERT INTO provium_inputs(pipeline,snapshot,inputs,dedup,state) "
                    "VALUES (%s,%s,%s,%s,'failed') RETURNING id",
                    ("existing", Jsonb(snapshot), Jsonb({}), dedup),
                ).fetchone()["id"]
        upgraded = PostgresBackend(dsn)
        assert upgraded.enqueue("existing", {}) == identity
        row = upgraded.pending("existing", include_finished=True)[0]
        assert row["queue_group"] == "default"
        assert row["state"] == "failed"
        assert upgraded.enqueue("existing", {}, queue_group="priority") != identity
        assert upgraded.pending("existing") == []
        assert upgraded.retry("existing") == 1
    finally:
        with db.connect() as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_queues_share_computations_without_consuming_each_other(db, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from test_runtime import configuration
    from test_store import make_artifact

    from provium_pipeline import ArtifactStore, ExecutionContext

    graph = configuration().model_copy(update={"name": "queues-" + uuid4().hex})
    db.register([graph.model_dump(mode="json")])
    source = str(make_artifact(tmp_path / "source.pa"))
    for name in ("priority", "batch", "untouched"):
        db.enqueue(graph.name, {"document": source}, queue_group=name)
    contexts = [
        ExecutionContext(
            graph,
            ArtifactStore(str(tmp_path / "store")),
            backend=db,
            queue_group=name,
            # Allow the other executor to finish worker startup on slower CI runners.
            idle_wait=5,
        )
        for name in ("priority", "batch")
    ]

    def run(context):
        with context:
            context.start()

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(run, contexts))
    assert sum(context.worker_starts for context in contexts) == 1
    assert all(context.outcomes[-1].state == "completed" for context in contexts)
    assert db.pending(graph.name, queue_group="untouched")
    assert not db.pending(graph.name, queue_group="priority")
    assert not db.pending(graph.name, queue_group="batch")


@pytest.mark.parametrize("command", ["enqueue", "retry", "queue", "run", "status"])
def test_disabled_default_fails_before_backend(command, tmp_path, monkeypatch, capsys):
    from unittest.mock import Mock

    from provium_pipeline.persistence import factory

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROVIUM_CONFIG__PIPELINE__QUEUE__DEFAULT_ENABLED", "false")
    opener = Mock(side_effect=AssertionError("backend must not load"))
    monkeypatch.setattr(factory, "open_backend", opener)
    group = "dispatch" if command in {"enqueue", "retry", "queue"} else "pipeline"
    assert cli.run([group, command, "example", "--database", "unused"]) == 2
    assert "--queue" in capsys.readouterr().err
    opener.assert_not_called()


@pytest.mark.parametrize("command", ["run", "status"])
def test_queue_not_silently_ignored_for_local_execution(command, capsys):
    assert cli.run(["pipeline", command, "-f", "missing.yaml", "-q", "priority"]) == 2
    assert "--queue requires a registered pipeline" in capsys.readouterr().err
