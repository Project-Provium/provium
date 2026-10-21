import json
import os
from uuid import uuid4

import pytest
from provium import cli
from test_store import make_artifact

from provium_pipeline import PipelineDefinition
from provium_pipeline.persistence.backends.postgres import PostgresBackend

DSN = os.environ.get("PROVIUM_TEST_DATABASE")


@pytest.fixture
def db():
    if not DSN:
        pytest.skip("requires PostgreSQL")
    return PostgresBackend(DSN)


def document(name):
    return {
        "kind": "PipelineConfiguration",
        "name": name,
        "inputs": {},
        "nodes": {},
        "outputs": {},
    }


def test_unregister_removes_queue_and_definition_but_preserves_shared_results(db):
    name = "p-" + uuid4().hex
    other = "p-" + uuid4().hex
    profile = {"kind": "ComputeProfile", "name": name}
    db.register(
        [document(name), document(other), profile],
        definitions={name: PipelineDefinition({}, {})},
    )
    identity = db.enqueue(name, {})
    other_id = db.enqueue(other, {})
    lease = db.acquire("test-" + name, "a" * 64, "run", input_id=identity)
    assert lease
    assert db.unregister([{"kind": "PipelineConfiguration", "name": name}]) == {
        "unregistered": 1,
        "removed_inputs": 1,
    }
    assert db.pending(name, include_finished=True) == []
    assert db.definition(name) is None
    assert db.get("ComputeProfile", name)["name"] == name
    assert db.pending(other)[0]["id"] == other_id
    assert db.complete(lease, {"result": "shared"})
    assert db.result(lease.namespace, lease.key) == {"result": "shared"}
    assert db.acquire("test-" + name, "b" * 64, "run", input_id=identity) is None
    assert db.unregister([{"kind": "PipelineConfiguration", "name": name}]) == {
        "unregistered": 0,
        "removed_inputs": 0,
    }
    with pytest.raises(ValueError, match="unregistered"):
        db.enqueue(name, {})
    db.register([document(name)], definitions={name: PipelineDefinition({}, {})})
    assert db.enqueue(name, {}) != identity


def test_registry_listing_and_queue_counts(db):
    name = "p-" + uuid4().hex
    db.register([document(name), {"kind": "ComputeProfile", "name": name}])
    assert {"kind": "PipelineConfiguration", "name": name} in db.registered()
    assert {"kind": "ComputeProfile", "name": name} in db.registered("ComputeProfile")
    assert all(
        row["kind"] == "PipelineConfiguration"
        for row in db.registered("PipelineConfiguration")
    )
    assert db.queue_info(name) == [
        {
            "pipeline": name,
            "queue": "default",
            "total": 0,
            "states": {
                "pending": 0,
                "partial": 0,
                "cancelled": 0,
                "failed": 0,
                "completed": 0,
            },
        }
    ]
    identity = db.enqueue(name, {})
    db.progress(identity, "failed", {})
    summary = db.queue_info(name)[0]
    assert summary["total"] == 1
    assert summary["states"]["failed"] == 1
    assert summary in db.queue_info()
    assert db.queue_info("missing-" + uuid4().hex) == []
    assert (
        db.unregister([{"kind": "ComputeProfile", "name": name}])["removed_inputs"] == 0
    )
    assert db.pending(name, include_finished=True)
    assert db.unregister([document(name)])["removed_inputs"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "Unknown", "name": "x"},
        {"kind": "ComputeProfile", "name": ""},
        {"kind": "ComputeProfile"},
        {"kind": "ComputeProfile", "name": 1},
    ],
)
def test_unregister_validates_entire_batch_before_deletion(db, bad):
    name = "profile-" + uuid4().hex
    item = {"kind": "ComputeProfile", "name": name}
    db.register([item])
    with pytest.raises(ValueError):
        db.unregister([item, bad])
    assert db.get("ComputeProfile", name)["name"] == name
    with pytest.raises(ValueError):
        db.registered("Unknown")


def test_enqueue_racing_unregister_does_not_restore_deleted_queue(
    db, tmp_path, monkeypatch
):
    from provium_pipeline.store import ArtifactStore

    name = "p-" + uuid4().hex
    db.register(
        [
            {
                **document(name),
                "inputs": {"x": "provium_text_pipeline_example.DocumentV1"},
            }
        ]
    )
    source = str(make_artifact(tmp_path / "source.pa"))
    original = ArtifactStore.inspect

    def inspect(store, location):
        result = original(store, location)
        db.unregister([document(name)])
        return result

    monkeypatch.setattr(ArtifactStore, "inspect", inspect)
    with pytest.raises(ValueError, match="changed|unregistered"):
        db.enqueue(name, {"x": source})
    assert db.pending(name, include_finished=True) == []


def test_management_cli(db, tmp_path, capsys):
    name = "p-" + uuid4().hex
    path = tmp_path / "bundle.yaml"
    path.write_text(
        f"kind: PipelineConfiguration\nname: {name}\n"
        "inputs: {}\nnodes: {}\noutputs: {}\n"
        f"---\nkind: ComputeProfile\nname: {name}\n"
    )
    common = ["--database", DSN]
    assert cli.run(["pipeline", "register", "-f", str(path), *common]) == 0
    db.enqueue(name, {})
    capsys.readouterr()
    assert cli.run(["pipeline", "list", "pipelines", *common]) == 0
    assert {"kind": "PipelineConfiguration", "name": name} in json.loads(
        capsys.readouterr().out
    )
    assert cli.run(["pipeline", "list", "profiles", *common]) == 0
    assert {"kind": "ComputeProfile", "name": name} in json.loads(
        capsys.readouterr().out
    )
    assert cli.run(["pipeline", "list", *common]) == 0
    capsys.readouterr()
    assert cli.run(["pipeline", "queue", name, *common]) == 0
    assert json.loads(capsys.readouterr().out)[0]["states"]["pending"] == 1
    assert cli.run(["pipeline", "queue", *common]) == 0
    capsys.readouterr()
    assert cli.run(["pipeline", "unregister", "-f", str(path), *common]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "unregistered": 2,
        "removed_inputs": 1,
    }
    assert cli.run(["pipeline", "register", "-f", str(path), *common]) == 0
    assert cli.run(["pipeline", "unregister", name, "--profile", name, *common]) == 0
    assert cli.run(["pipeline", "unregister", *common]) == 2
    assert cli.run(["pipeline", "unregister", name, "-f", str(path), *common]) == 2
    assert cli.run(["pipeline", "unregister", "--profile", name, *common]) == 0
    assert cli.run(["pipeline", "unregister", name, *common]) == 0


def test_management_requires_database(monkeypatch):
    monkeypatch.delenv("PROVIUM_DATABASE_URL", raising=False)
    for command in (["list"], ["queue"], ["unregister", "missing"]):
        assert cli.run(["pipeline", *command]) == 2


@pytest.mark.parametrize("phase", ["before_admission", "after_admission"])
def test_unregister_stops_loaded_pending_work_but_allows_admitted_result(
    db, tmp_path, monkeypatch, phase
):
    from test_runtime import configuration

    from provium_pipeline import ArtifactStore, ExecutionContext

    graph = configuration().model_copy(update={"name": "p-" + uuid4().hex})
    db.register([graph.model_dump(mode="json")])
    for index in range(2):
        source = make_artifact(tmp_path / f"{index}.pa", str(index))
        db.enqueue(graph.name, {"document": str(source)})
    accepted = []
    with ArtifactStore(str(tmp_path / "store")) as store:
        with ExecutionContext(graph, store, backend=db) as context:
            if phase == "before_admission":
                load_page = context._load_page

                def remove_after_scan(rows):
                    load_page(rows)
                    db.unregister([graph.model_dump(mode="json")])

                monkeypatch.setattr(context, "_load_page", remove_after_scan)
            else:
                complete = db.complete

                def remove_before_commit(lease, outputs):
                    db.unregister([graph.model_dump(mode="json")])
                    result = complete(lease, outputs)
                    accepted.append((lease, outputs))
                    return result

                monkeypatch.setattr(db, "complete", remove_before_commit)
            context.start()
        assert context.error is None
        assert context.worker_starts == (0 if phase == "before_admission" else 1)
        assert len(accepted) == (0 if phase == "before_admission" else 1)
        for lease, outputs in accepted:
            assert db.result(lease.namespace, lease.key) == outputs
    assert db.pending(graph.name, include_finished=True) == []
