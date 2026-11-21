import boto3
import pytest
from botocore.stub import ANY, Stubber

from provium_cloud.deployment import CloudDeployment, JobSettings
from provium_cloud.execution.aws.batch import BatchExecutor
from provium_cloud.state.models import Generation


@pytest.fixture
def batch():
    client = boto3.client(
        "batch",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    return BatchExecutor("us-east-1", client=client)


def test_submit_and_reconcile_by_attempt(batch):
    deployment = CloudDeployment.model_validate(
        {
            "provider": "aws",
            "region": "us-east-1",
            "state": {"backend": "custom"},
            "artifacts": {"backend": "custom"},
        }
    )
    generation = Generation(
        number=1,
        attempt="abc",
        payload={},
        settings=JobSettings(job_queue="cpu", job_definition="worker:1", timeout=60),
        created_at=1,
        updated_at=1,
    )
    with Stubber(batch.client) as stub:
        stub.add_response(
            "submit_job",
            {"jobId": "job-1", "jobName": "provium-abc"},
            {
                "jobName": "provium-abc",
                "jobQueue": "cpu",
                "jobDefinition": "worker:1",
                "containerOverrides": ANY,
                "timeout": {"attemptDurationSeconds": 60},
                "retryStrategy": {"attempts": 1},
                "tags": ANY,
            },
        )
        assert batch.submit("key", generation, deployment) == "job-1"
        stub.add_response(
            "list_jobs",
            {
                "jobSummaryList": [{"jobId": "job-1", "jobName": "provium-abc"}],
                "nextToken": "next",
            },
            {
                "jobQueue": "cpu",
                "filters": [{"name": "JOB_NAME", "values": ["provium-abc"]}],
            },
        )
        stub.add_response(
            "list_jobs",
            {"jobSummaryList": []},
            {
                "jobQueue": "cpu",
                "filters": [{"name": "JOB_NAME", "values": ["provium-abc"]}],
                "nextToken": "next",
            },
        )
        assert batch.find(generation) == ["job-1"]
        stub.assert_no_pending_responses()


def test_status_and_cancel_running_or_pending(batch):
    with Stubber(batch.client) as stub:
        stub.add_response("describe_jobs", {"jobs": []}, {"jobs": ["missing"]})
        assert batch.describe(["missing"]) == {}
        stub.add_response(
            "cancel_job", {}, {"jobId": "waiting", "reason": "run canceled"}
        )
        batch.cancel("waiting", "RUNNABLE", "run canceled")
        stub.add_response(
            "terminate_job", {}, {"jobId": "running", "reason": "run canceled"}
        )
        batch.cancel("running", "RUNNING", "run canceled")
        batch.cancel("done", "SUCCEEDED", "ignored")
        assert batch.describe([]) == {}
        stub.assert_no_pending_responses()


def test_default_client_disables_unsafe_submission_retries(monkeypatch):
    from unittest.mock import Mock

    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(boto3, "client", factory)
    executor = BatchExecutor("us-west-2")
    assert executor.client is client
    config = factory.call_args.kwargs["config"]
    assert config.retries == {"total_max_attempts": 1}
    assert config.read_timeout < 120


def test_logs_pages_and_unknown_job(batch, monkeypatch):
    logs = boto3.client(
        "logs",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: logs)
    job = {
        "jobId": "job",
        "jobName": "name",
        "jobQueue": "queue",
        "status": "RUNNING",
        "startedAt": 1,
        "jobDefinition": "definition",
        "container": {
            "logStreamName": "stream",
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {"awslogs-group": "custom"},
            },
        },
    }
    with Stubber(batch.client) as stub, Stubber(logs) as log_stub:
        with pytest.raises(ValueError):
            batch.logs("job", limit=0)
        stub.add_response("describe_jobs", {"jobs": []}, {"jobs": ["missing"]})
        with pytest.raises(LookupError):
            batch.logs("missing")
        empty = {**job, "container": {}}
        stub.add_response("describe_jobs", {"jobs": [empty]}, {"jobs": ["job"]})
        assert batch.logs("job")["events"] == []
        for cursor in (None, "cursor"):
            stub.add_response("describe_jobs", {"jobs": [job]}, {"jobs": ["job"]})
            params = {
                "logGroupName": "custom",
                "logStreamName": "stream",
                "startFromHead": True,
                "limit": 100,
            }
            if cursor:
                params["nextToken"] = cursor
            log_stub.add_response(
                "get_log_events",
                {
                    "events": [
                        {"timestamp": 1, "message": "hello", "ingestionTime": 2}
                    ],
                    "nextForwardToken": "next",
                },
                params,
            )
            assert batch.logs("job", cursor=cursor)["events"][0]["message"] == "hello"
        stub.add_response("describe_jobs", {"jobs": [job]}, {"jobs": ["job"]})
        assert batch.describe(["job"]) == {"job": "RUNNING"}
        stub.assert_no_pending_responses()
        log_stub.assert_no_pending_responses()


def test_submit_bootstrap_contains_configuration_without_secrets(batch):
    from unittest.mock import Mock

    deployment = CloudDeployment.model_validate(
        {
            "provider": "aws",
            "region": "us-east-1",
            "state": {
                "backend": "postgres",
                "options": {"connection_env": "DATABASE_URL"},
            },
            "artifacts": {
                "backend": "s3",
                "options": {"location": "s3://bucket/prefix"},
            },
        }
    )
    generation = Generation(
        number=2,
        attempt="retry",
        payload={},
        settings=JobSettings(job_queue="cpu", job_definition="worker:1"),
        created_at=1,
        updated_at=1,
    )
    batch.client = Mock()
    batch.client.submit_job.return_value = {"jobId": "job"}
    assert batch.submit("key", generation, deployment) == "job"
    arguments = batch.client.submit_job.call_args.kwargs
    assert "timeout" not in arguments
    assert arguments["containerOverrides"]["command"][-1] == "retry"
    value = arguments["containerOverrides"]["environment"][0]["value"]
    assert CloudDeployment.model_validate_json(value) == deployment
