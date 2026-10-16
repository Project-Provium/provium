import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from provium_pipeline.database import PostgresBackend

DSN = os.environ.get("PROVIUM_TEST_DATABASE")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set PROVIUM_TEST_DATABASE for PostgreSQL integration"
)


def backend():
    return PostgresBackend(DSN)


def test_concurrent_enqueue_deduplicates_and_registration_is_immutable(tmp_path):
    from test_store import make_artifact

    artifact = str(make_artifact(tmp_path / "source.pa"))
    db = backend()
    name = "p-" + uuid4().hex
    document = {
        "kind": "PipelineConfiguration",
        "name": name,
        "nodes": {},
        "outputs": {},
    }
    db.register([document])
    db.register([document])
    with pytest.raises(ValueError):
        db.register([{**document, "outputs": {"x": "$inputs.x"}}])
    with ThreadPoolExecutor(4) as pool:
        ids = list(pool.map(lambda _: db.enqueue(name, {"x": artifact}), range(8)))
    assert len(set(ids)) == 1


def test_lease_fencing_and_force_preserves_previous_result():
    db = backend()
    namespace, key = "test-" + uuid4().hex, "a" * 64
    first = db.acquire(namespace, key, "run1", force=False)
    assert first
    assert db.acquire(namespace, key, "run2", force=False) is None
    assert db.complete(first, {"output": {"location": "old"}})
    assert db.result(namespace, key) == {"output": {"location": "old"}}
    assert db.acquire(namespace, key, "run2", force=False) is None
    second = db.acquire(namespace, key, "run2", force=True)
    assert second
    assert not db.complete(first, {"output": {"location": "stale"}})
    db.fail(second, "intentional failure")
    assert db.result(namespace, key) == {"output": {"location": "old"}}
    assert db.acquire(namespace, key, "run2", force=True) is None


def test_expired_owner_cannot_renew_or_commit():
    import time

    db = backend()
    lease = db.acquire("test-" + uuid4().hex, "b" * 64, "run", ttl=0.05)
    assert lease
    time.sleep(0.08)
    assert not db.renew(lease, ttl=1)
    assert not db.complete(lease, {})


def test_expired_force_restores_previous_committed_result():
    import time

    db = backend()
    namespace = "test-" + uuid4().hex
    first = db.acquire(namespace, "c" * 64, "first")
    assert first
    assert db.complete(first, {"output": "old"})
    second = db.acquire(namespace, "c" * 64, "second", force=True, ttl=0.03)
    assert second
    time.sleep(0.05)
    assert db.result(namespace, "c" * 64) == {"output": "old"}
    assert not db.complete(second, {"output": "stale"})


def test_enqueue_pins_artifact_content(tmp_path):
    from test_store import make_artifact

    db = backend()
    name = "p-" + uuid4().hex
    db.register(
        [
            {
                "kind": "PipelineConfiguration",
                "name": name,
                "nodes": {},
                "outputs": {"result": "$inputs.x"},
            }
        ]
    )
    source = make_artifact(tmp_path / "source.pa")
    identity = db.enqueue(name, {"x": str(source)})
    row = db.pending(name)[0]
    assert row["id"] == identity
    assert row["inputs"]["x"]["fingerprint"]["content"]


def test_registered_definition_is_pinned_and_checked(tmp_path):
    from provium_text_pipeline_example.artifact import DOCUMENT
    from test_store import make_artifact

    from provium_pipeline import PipelineDefinition

    db = backend()
    name = "p-" + uuid4().hex
    definition = PipelineDefinition(
        inputs={"x": DOCUMENT},
        outputs={"result": DOCUMENT},
        profiles=frozenset({"cluster"}),
    )
    document = {
        "kind": "PipelineConfiguration",
        "name": name,
        "nodes": {},
        "outputs": {"result": "$inputs.x"},
    }
    db.register([document], definitions={name: definition})
    assert db.definition(name).profiles == frozenset({"cluster"})
    db.enqueue(name, {"x": str(make_artifact(tmp_path / "input.pa"))})
    assert db.pending(name)[0]["definition"]["profiles"] == ["cluster"]
    with pytest.raises(ValueError, match="definition"):
        db.register([document], definitions={name: PipelineDefinition({}, {})})


def test_migrations_serialize_and_reject_newer_versions():
    import psycopg
    from psycopg import sql

    from provium_pipeline.database import MIGRATIONS

    schema = "test_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    connection_string = psycopg.conninfo.make_conninfo(
        DSN, options=f"-c search_path={schema}"
    )
    try:
        with ThreadPoolExecutor(4) as pool:
            databases = list(
                pool.map(lambda _: PostgresBackend(connection_string), range(4))
            )
        db = databases[0]
        with db.connect() as connection:
            assert connection.execute(
                "SELECT count(*) AS total FROM provium_migrations"
            ).fetchone()["total"] == len(MIGRATIONS)
            connection.execute(
                "INSERT INTO provium_migrations VALUES (%s)", (len(MIGRATIONS) + 1,)
            )
        with pytest.raises(ValueError, match="unsupported"):
            PostgresBackend(connection_string)
        with db.connect() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) AS total FROM provium_migrations"
                ).fetchone()["total"]
                == len(MIGRATIONS) + 1
            )
    finally:
        with psycopg.connect(DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_failed_migration_rolls_back_schema_and_version(monkeypatch):
    import psycopg
    from psycopg import sql

    import provium_pipeline.database as module

    schema = "test_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    connection_string = psycopg.conninfo.make_conninfo(
        DSN, options=f"-c search_path={schema}"
    )
    try:
        monkeypatch.setattr(
            module, "MIGRATIONS", ["CREATE TABLE should_rollback(id int); SELECT 1/0;"]
        )
        with pytest.raises(psycopg.errors.DivisionByZero):
            PostgresBackend(connection_string)
        with psycopg.connect(connection_string) as connection:
            assert (
                connection.execute("SELECT to_regclass('should_rollback')").fetchone()[
                    0
                ]
                is None
            )
            assert (
                connection.execute(
                    "SELECT to_regclass('provium_migrations')"
                ).fetchone()[0]
                is None
            )
    finally:
        with psycopg.connect(DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_cancelled_input_remains_available_for_later_run(tmp_path):
    from test_store import make_artifact

    db = backend()
    name = "p-" + uuid4().hex
    db.register(
        [
            {
                "kind": "PipelineConfiguration",
                "name": name,
                "nodes": {},
                "outputs": {"result": "$inputs.x"},
            }
        ]
    )
    identity = db.enqueue(name, {"x": str(make_artifact(tmp_path / "source.pa"))})
    db.progress(identity, "cancelled", {"run": "cancelled-run"})
    assert [row["id"] for row in db.pending(name)] == [identity]
