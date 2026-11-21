"""Opt-in end-to-end assertions executed by the private AWS Batch test driver."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from provium.session import session
from provium_text_pipeline_example.artifact.word_count import WordCountV1Artifact

from provium_cloud import CloudClient
from provium_cloud.deployment import CloudDeployment
from provium_cloud.execution.controller import Controller
from provium_cloud.state.models import Run

GRAPH = {
    "inputs": {"document": "provium_text_pipeline_example.DocumentV1"},
    "nodes": {
        "tokens": {
            "procedure": "provium_text_pipeline_example.TokenizeV1",
            "inputs": {"source": "$inputs.document"},
        },
        "count": {
            "procedure": "provium_text_pipeline_example.CountWordsV1",
            "inputs": {"tokens": "$nodes.tokens.destination"},
        },
    },
    "targets": {"count": "$nodes.count.word_count"},
}


def complete(client: CloudClient, *runs: Run) -> list[Run]:
    controller = Controller(client)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        controller.tick()
        results = [client.get_run(run.id) for run in runs]
        for result in results:
            assert result.status not in ("failed", "canceled"), result.error
        if all(result.status == "succeeded" for result in results):
            return results
        time.sleep(3)
    pytest.fail("Live pipeline did not complete in 10 minutes")


def test_pipeline_procedure_deduplication_force_and_management(tmp_path: Path):
    deployment = CloudDeployment.model_validate(
        {
            "provider": "aws",
            "region": os.environ["AWS_DEFAULT_REGION"],
            "state": {
                "backend": "postgres",
                "options": {"connection_env": "PROVIUM_CLOUD_DATABASE_URL"},
            },
            "artifacts": {
                "backend": "s3",
                "options": {"location": f"s3://{os.environ['TEST_BUCKET']}/artifacts"},
            },
            "defaults": {
                "job_queue": os.environ["TEST_QUEUE"],
                "job_definition": os.environ["TEST_WORKER"],
                "timeout": 300,
            },
        }
    )
    config = tmp_path / "deployment.json"
    config.write_text(deployment.model_dump_json())
    text, document = tmp_path / "document.txt", tmp_path / "document.pa"
    text.write_text("one two two", encoding="utf-8")
    subprocess.run(
        [
            "provium",
            "artifact",
            "load",
            "provium_text_pipeline_example.DocumentV1",
            str(text),
            str(document),
        ],
        check=True,
    )
    with CloudClient(deployment) as client:
        first = client.enqueue_pipeline(
            GRAPH, {"document": str(document)}, request_id="first"
        )
        assert (
            client.enqueue_pipeline(
                GRAPH, {"document": str(document)}, request_id="first"
            ).id
            == first.id
        )
        second = client.enqueue_procedure(
            "provium_text_pipeline_example.TokenizeV1", {"source": str(document)}
        )
        # A different graph shape must reuse the shared tokenize computation.
        shared = client.enqueue_pipeline(
            {
                "inputs": GRAPH["inputs"],
                "nodes": {"other": GRAPH["nodes"]["tokens"]},
                "targets": {"tokens": "$nodes.other.destination"},
            },
            {"document": str(document)},
        )
        first, second, shared = complete(client, first, second, shared)
        assert first.progress["completed"] == first.progress["total"] == 2
        assert (
            first.tasks["tokens"].computation
            == second.tasks["procedure"].computation
            == shared.tasks["other"].computation
        )
        for node in first.tasks:
            computation = client.attempts(first.id, node)
            assert computation is not None
            assert len(computation.generations) == 1
            assert len(computation.generations[0].job_ids) == 1
        output = tmp_path / "count.pa"
        client.fetch(first.id, "count", str(output))
        with session():
            assert WordCountV1Artifact.open(output).read() == 3
        cached = complete(
            client, client.enqueue_pipeline(GRAPH, {"document": str(document)})
        )[0]
        assert cached.progress["reused"] == 2
        forced = client.enqueue_pipeline(GRAPH, {"document": str(document)}, force=True)
        duplicate = client.enqueue_pipeline(
            GRAPH, {"document": str(document)}, force=True
        )
        forced, duplicate = complete(client, forced, duplicate)
        for node in forced.tasks:
            computation = client.attempts(forced.id, node)
            assert computation is not None
            assert len(computation.generations) == 2
            assert len(computation.generations[-1].job_ids) == 1
            assert (
                forced.tasks[node].generation == duplicate.tasks[node].generation == 2
            )
        # The native CLI uses the same PostgreSQL state and S3 artifacts.
        args = ["provium", "cloud", "aws"]
        response = subprocess.run(
            [
                *args,
                "enqueue",
                "-d",
                str(config),
                "--procedure",
                "provium_text_pipeline_example.TokenizeV1",
                "--input",
                f"source={document}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        identity = json.loads(response.stdout)["id"]
        client.cancel(identity)
        assert client.get_run(identity).status == "canceled"
        complete(client, client.retry(identity))
        status = subprocess.run(
            [*args, "status", "-d", str(config), identity],
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(status.stdout)["status"] == "succeeded"
        listed = client.list_runs(input_set_id=first.input_set_id)
        assert first.id in {item.id for item in listed.items}
