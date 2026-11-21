"""Real service-backed orchestration without AWS account credentials."""

import uuid

import boto3
import pytest
from cloud_fixtures import LocalBatch, finish, graph
from testcontainers.community.localstack import LocalStackContainer
from testcontainers.community.postgres import PostgresContainer

from provium_cloud import CloudClient
from provium_cloud.deployment import CloudDeployment
from provium_cloud.execution.controller import Controller


@pytest.mark.integration
def test_s3_and_postgres_pipeline(tmp_path, document, monkeypatch):
    with (
        LocalStackContainer(
            image="localstack/localstack:4", region_name="us-east-1"
        ).with_services("s3") as s3,
        PostgresContainer("postgres:17-alpine", driver=None) as postgres,
    ):
        for name, value in {
            "AWS_ENDPOINT_URL_S3": s3.get_url(),
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_DEFAULT_REGION": "us-east-1",
            "CLOUD_TEST_DATABASE": postgres.get_connection_url(),
        }.items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
        bucket = "cloud-test-" + uuid.uuid4().hex
        sdk = boto3.client("s3")
        sdk.create_bucket(Bucket=bucket)
        deployment = CloudDeployment.model_validate(
            {
                "provider": "aws",
                "region": "us-east-1",
                "state": {
                    "backend": "postgres",
                    "options": {"connection_env": "CLOUD_TEST_DATABASE"},
                },
                "artifacts": {
                    "backend": "s3",
                    "options": {"location": f"s3://{bucket}/artifacts"},
                },
                "defaults": {"job_queue": "cpu", "job_definition": "worker:1"},
            }
        )
        executor = LocalBatch()
        with CloudClient(deployment, executor=executor) as client:
            controller = Controller(client)
            run = client.enqueue_pipeline(graph(), {"document": str(document)})
            result = finish(client, controller, executor, run)
            assert result.status == "succeeded", result.error
            client.fetch(run.id, "count", str(tmp_path / "result.pa"))
            assert (tmp_path / "result.pa").exists()
            again = client.enqueue_pipeline(graph(), {"document": str(document)})
            assert finish(client, controller, executor, again).status == "succeeded"
            assert executor.submissions == 2
            # Deleted S3 outputs are recomputed, not treated as a valid cache hit.
            from urllib.parse import urlparse

            location = urlparse(result.outputs["count"]["location"])
            sdk.delete_object(Bucket=bucket, Key=location.path.lstrip("/"))
            retry = client.enqueue_pipeline(graph(), {"document": str(document)})
            assert finish(client, controller, executor, retry).status == "succeeded"
            assert executor.submissions == 3
