import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pipeline_fixtures import SLOW, SOURCE
from test_runtime import configuration
from test_store import make_artifact

from provium_pipeline import (
    ArtifactStore,
    ComputeProfile,
    ExecutionContext,
    InputSet,
    PipelineConfiguration,
    PostgresBackend,
)

DSN = os.environ.get("PROVIUM_TEST_DATABASE")


def test_procedure_isolated_in_child_process(tmp_path):
    import provium
    from provium_text_pipeline_example.artifact import DOCUMENT

    graph = PipelineConfiguration(
        name="source",
        nodes={"source": {"procedure": SOURCE.identifier}},
        outputs={"result": "$nodes.source.result"},
    )
    with ExecutionContext(
        graph,
        ArtifactStore(str(tmp_path / "store")),
        procedures={SOURCE.identifier: SOURCE},
    ) as context:
        context.start([InputSet({})])
    artifact = context.outcomes[0].outputs["result"]
    with provium.session():
        with DOCUMENT.resolve().bind_read(artifact.path).open() as reader:
            assert int(reader.read()) != os.getpid()


def test_hard_limit_interrupts_running_code(tmp_path):
    graph = PipelineConfiguration(
        name="slow",
        nodes={"slow": {"procedure": SLOW.identifier}},
        outputs={"result": "$nodes.slow.result"},
    )
    started = time.monotonic()
    with ExecutionContext(
        graph,
        ArtifactStore(str(tmp_path / "store")),
        procedures={SLOW.identifier: SLOW},
        profile=ComputeProfile(name="test", hard_time_limit=0.4),
    ) as context:
        context.start([InputSet({})])
    assert time.monotonic() - started < 1.5
    assert context.error is None
    assert context.outcomes[0].state == "failed"


@pytest.mark.skipif(
    not DSN, reason="PostgreSQL integration requires PROVIUM_TEST_DATABASE"
)
def test_two_pipelines_share_one_computation(tmp_path):
    backend = PostgresBackend(DSN)
    graphs = [
        configuration().model_copy(update={"name": "p-" + uuid4().hex})
        for _ in range(2)
    ]
    backend.register([graph.model_dump(mode="json") for graph in graphs])
    source = str(make_artifact(tmp_path / "input.pa"))
    for graph in graphs:
        backend.enqueue(graph.name, {"document": source})
    contexts = [
        ExecutionContext(
            graph, ArtifactStore(str(tmp_path / "store")), backend=backend, idle_wait=1
        )
        for graph in graphs
    ]
    with ThreadPoolExecutor(2) as pool:

        def run(context):
            with context:
                context.start()

        list(pool.map(run, contexts))
    assert sum(context.worker_starts for context in contexts) == 1
    assert all(context.outcomes[0].state == "completed" for context in contexts)


@pytest.mark.skipif(
    not os.environ.get("PROVIUM_TEST_S3"),
    reason="LocalStack integration requires PROVIUM_TEST_S3",
)
def test_s3_execution_verification_force_and_status(tmp_path):
    import boto3

    bucket = "provium-" + uuid4().hex
    client = boto3.client("s3")
    client.create_bucket(Bucket=bucket)
    try:
        store = ArtifactStore(f"s3://{bucket}/artifacts", scratch=tmp_path / "scratch")
        item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
        results = []
        for force in ([], [], ["all"]):
            with ExecutionContext(configuration(), store, force=force) as context:
                context.start([item])
            assert context.outcomes[0].state == "completed"
            results.append(context.outcomes[0].outputs["tokens"])
        assert results[0].location == results[1].location
        assert results[2].location != results[1].location
        assert results[2].fingerprint == results[1].fingerprint
    finally:
        objects = client.list_objects_v2(Bucket=bucket).get("Contents", [])
        if objects:
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
            )
        client.delete_bucket(Bucket=bucket)


@pytest.mark.skipif(
    not DSN, reason="PostgreSQL integration requires PROVIUM_TEST_DATABASE"
)
def test_queue_scans_pages_and_reuses_resident_worker(tmp_path):
    backend = PostgresBackend(DSN)
    graph = configuration().model_copy(update={"name": "p-" + uuid4().hex})
    backend.register([graph.model_dump(mode="json")])
    for index in range(5):
        source = str(make_artifact(tmp_path / f"{index}.pa", str(index)))
        backend.enqueue(graph.name, {"document": source})
    with ExecutionContext(
        graph, ArtifactStore(str(tmp_path / "store")), backend=backend
    ) as context:
        context.queue_page_size = 2
        context.start()
    assert len(context.outcomes) == 5
    assert all(outcome.state == "completed" for outcome in context.outcomes)
    assert context.worker_starts == 1
    assert context.max_resident_inputs <= 2


@pytest.mark.skipif(not DSN, reason="requires PostgreSQL")
def test_lost_commit_acknowledgement_does_not_fail_or_reexecute(tmp_path, monkeypatch):
    backend = PostgresBackend(DSN)
    graph = configuration().model_copy(update={"name": "p-" + uuid4().hex})
    backend.register([graph.model_dump(mode="json")])
    backend.enqueue(graph.name, {"document": str(make_artifact(tmp_path / "input.pa"))})
    complete = backend.complete
    calls = []

    def lost_ack(lease, outputs):
        assert complete(lease, outputs)
        calls.append(lease.key)
        raise OSError("commit acknowledgement lost")

    monkeypatch.setattr(backend, "complete", lost_ack)
    with ExecutionContext(
        graph, ArtifactStore(str(tmp_path / "store")), backend=backend
    ) as context:
        context.start()
    assert context.outcomes[0].state == "completed"
    assert len(calls) == 1
    assert context.worker_starts == 1


@pytest.mark.skipif(not DSN, reason="requires PostgreSQL")
def test_status_uses_recorded_store_after_default_changes(tmp_path, capsys):
    import json

    from provium_pipeline.cli import run

    backend = PostgresBackend(DSN)
    graph = configuration().model_copy(update={"name": "p-" + uuid4().hex})
    backend.register([graph.model_dump(mode="json")])
    backend.enqueue(graph.name, {"document": str(make_artifact(tmp_path / "input.pa"))})
    with ExecutionContext(
        graph, ArtifactStore(str(tmp_path / "original")), backend=backend
    ) as context:
        context.start()
    assert (
        run(
            [
                "status",
                graph.name,
                "--database",
                DSN,
                "--outputs",
                "--store",
                str(tmp_path / "different"),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result[0]["state"] == "completed"
    assert (
        result[0]["outputs"]["tokens"]["location"]
        == context.outcomes[0].outputs["tokens"].location
    )


@pytest.mark.skipif(not os.environ.get("PROVIUM_TEST_S3"), reason="requires LocalStack")
def test_s3_lost_upload_acknowledgement_reconciles(tmp_path, monkeypatch):
    import boto3

    client = boto3.client("s3")
    bucket = "provium-" + uuid4().hex
    client.create_bucket(Bucket=bucket)
    try:
        with ArtifactStore(
            f"s3://{bucket}/artifacts", scratch=tmp_path / "scratch"
        ) as store:
            upload = store.client.upload_file

            def lost_ack(*args, **kwargs):
                upload(*args, **kwargs)
                raise OSError("upload acknowledgement lost")

            monkeypatch.setattr(store.client, "upload_file", lost_ack)
            with ExecutionContext(configuration(), store) as context:
                context.start(
                    [InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})]
                )
            assert context.outcomes[0].state == "completed"
            assert context.worker_starts == 1
    finally:
        objects = client.list_objects_v2(Bucket=bucket).get("Contents", [])
        if objects:
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
            )
        client.delete_bucket(Bucket=bucket)
