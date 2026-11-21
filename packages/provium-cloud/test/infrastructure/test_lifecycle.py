import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from infrastructure.aws.config import Settings
from infrastructure.aws.lifecycle import Environment


@pytest.fixture
def environment(tmp_path):
    session = MagicMock()
    clients = {
        name: MagicMock()
        for name in ("sts", "cloudformation", "batch", "s3", "logs", "ecr")
    }
    session.client.side_effect = lambda name, **kw: clients[name]
    clients["sts"].get_caller_identity.return_value = {"Account": "123456789012"}
    env = Environment(
        Settings("us-east-1", "123456789012", "arn:aws:iam::123456789012:role/test"),
        tmp_path,
        session=session,
    )
    return env, clients


def test_wrong_account_never_creates_or_deletes(environment):
    env, clients = environment
    clients["sts"].get_caller_identity.return_value = {"Account": "999999999999"}
    with pytest.raises(ValueError, match="account"):
        env.create()
    clients["cloudformation"].create_stack.assert_not_called()
    clients["cloudformation"].delete_stack.assert_not_called()


def test_create_records_intent_before_request_and_retains_recovery(environment):
    env, clients = environment

    def fail(**kwargs):
        journal = json.loads((env.directory / "state.json").read_text())
        assert journal["stack"] == kwargs["StackName"]
        assert journal["token"] == kwargs["ClientRequestToken"]
        assert kwargs["Capabilities"] == ["CAPABILITY_IAM"]
        raise RuntimeError("network failure")

    clients["cloudformation"].create_stack.side_effect = fail
    with pytest.raises(RuntimeError):
        env.create()
    assert (env.directory / "state.json").stat().st_mode & 0o777 == 0o600


def test_cleanup_refuses_unowned_stack(environment):
    env, clients = environment
    env.journal = {"stack": "foreign", "token": "test"}
    clients["cloudformation"].describe_stacks.return_value = {
        "Stacks": [{"Tags": [], "StackStatus": "CREATE_COMPLETE"}]
    }
    with pytest.raises(ValueError, match="ownership"):
        env.cleanup()
    clients["cloudformation"].delete_stack.assert_not_called()
    clients["s3"].delete_objects.assert_not_called()


def test_missing_stack_cleanup_is_idempotent_but_access_denied_is_not(environment):
    env, clients = environment
    env.journal = {"stack": "test", "token": "token"}
    cf = clients["cloudformation"]
    cf.describe_stacks.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationError",
                "Message": "Stack with id test does not exist",
            }
        },
        "DescribeStacks",
    )
    env.cleanup()
    assert env.journal["cleaned"]
    env.journal.pop("cleaned")
    cf.describe_stacks.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "DescribeStacks"
    )
    with pytest.raises(ClientError):
        env.cleanup()


def test_cleanup_stops_jobs_before_emptying_and_deleting(environment):
    env, clients = environment
    env.journal = {"stack": "test", "token": "token"}
    cf, batch, s3 = (clients[k] for k in ("cloudformation", "batch", "s3"))
    cf.describe_stacks.return_value = {
        "Stacks": [
            {
                "Tags": [{"Key": "provium-cloud:test-run", "Value": "token"}],
                "StackStatus": "CREATE_COMPLETE",
            }
        ]
    }
    cf.get_paginator.return_value.paginate.return_value = [
        {
            "StackResourceSummaries": [
                {
                    "LogicalResourceId": "Queue",
                    "PhysicalResourceId": "queue",
                    "ResourceStatus": "CREATE_COMPLETE",
                },
                {
                    "LogicalResourceId": "Artifacts",
                    "PhysicalResourceId": "bucket",
                    "ResourceStatus": "CREATE_COMPLETE",
                },
            ]
        }
    ]
    calls = []
    batch.update_job_queue.side_effect = lambda **kw: calls.append("disable")
    batch.get_paginator.return_value.paginate.return_value = [{"jobSummaryList": []}]
    s3.get_paginator.return_value.paginate.return_value = [
        {
            "Versions": [{"Key": "a", "VersionId": "v"}],
            "DeleteMarkers": [{"Key": "a", "VersionId": "d"}],
        }
    ]
    s3.delete_objects.side_effect = lambda **kw: calls.append("empty") or {}
    cf.delete_stack.side_effect = lambda **kw: calls.append("delete")
    env.cleanup()
    assert calls == ["disable", "empty", "delete"]
    assert env.journal["cleaned"]
    assert s3.delete_objects.call_args.kwargs["Delete"]["Objects"] == [
        {"Key": "a", "VersionId": "v"},
        {"Key": "a", "VersionId": "d"},
    ]


def test_s3_partial_delete_failure_prevents_stack_deletion(environment):
    env, clients = environment
    clients["s3"].get_paginator.return_value.paginate.return_value = [
        {"Versions": [{"Key": "a", "VersionId": "v"}]}
    ]
    clients["s3"].delete_objects.return_value = {"Errors": [{"Code": "AccessDenied"}]}
    with pytest.raises(RuntimeError, match="empty"):
        env.empty_bucket("bucket")


def test_run_tears_down_after_build_failure(environment, monkeypatch):
    env, _ = environment
    monkeypatch.setattr(
        env, "create", MagicMock(return_value={"RepositoryUri": "image"})
    )
    cleanup = MagicMock()
    monkeypatch.setattr(env, "cleanup", cleanup)
    monkeypatch.setattr(env, "collect", MagicMock())
    monkeypatch.setattr(
        "infrastructure.aws.lifecycle.build_image",
        MagicMock(side_effect=RuntimeError("build failed")),
    )
    with pytest.raises(RuntimeError, match="build failed"):
        env.run()
    cleanup.assert_called_once()


def test_teardown_runs_even_when_collection_fails(environment, monkeypatch):
    env, _ = environment
    monkeypatch.setattr(
        env, "create", MagicMock(return_value={"RepositoryUri": "image"})
    )
    monkeypatch.setattr("infrastructure.aws.lifecycle.build_image", MagicMock())
    monkeypatch.setattr(
        env, "execute", MagicMock(side_effect=TimeoutError("timed out"))
    )
    monkeypatch.setattr(
        env, "collect", MagicMock(side_effect=RuntimeError("logs unavailable"))
    )
    cleanup = MagicMock()
    monkeypatch.setattr(env, "cleanup", cleanup)
    with pytest.raises(ExceptionGroup) as errors:
        env.run()
    assert len(errors.value.exceptions) == 2
    cleanup.assert_called_once()


def test_cleanup_cancels_all_active_statuses_and_waits(environment, monkeypatch):
    env, clients = environment
    batch = clients["batch"]
    pages = batch.get_paginator.return_value.paginate
    pages.side_effect = [
        [{"jobSummaryList": [{"jobId": f"job-{n}"}]}] for n in range(5)
    ] + [[{"jobSummaryList": []}]] * 5
    monkeypatch.setattr("infrastructure.aws.lifecycle.time.sleep", lambda _: None)
    env.stop_jobs("queue")
    assert {call.kwargs["jobId"] for call in batch.terminate_job.call_args_list} == {
        f"job-{n}" for n in range(5)
    }
    assert pages.call_count == 10


def test_driver_failure_and_timeout_do_not_report_success(environment, monkeypatch):
    env, clients = environment
    env.journal = {"token": "run"}
    batch = clients["batch"]
    batch.submit_job.return_value = {"jobId": "driver"}
    batch.describe_jobs.return_value = {
        "jobs": [{"status": "FAILED", "statusReason": "test failed"}]
    }
    with pytest.raises(RuntimeError, match="test failed"):
        env.execute({"Queue": "q", "Driver": "d"})
    assert not env.journal.get("passed")
    times = iter([0, 100000])
    monkeypatch.setattr(
        "infrastructure.aws.lifecycle.time.monotonic", lambda: next(times)
    )
    with pytest.raises(TimeoutError):
        env.execute({"Queue": "q", "Driver": "d"})


def test_existing_journal_is_not_overwritten(environment):
    env, clients = environment
    (env.directory / "state.json").write_text("{}")
    with pytest.raises(ValueError, match="already has a run"):
        env.create()
    clients["cloudformation"].create_stack.assert_not_called()
    assert (env.directory / "state.json").read_text() == "{}"


def test_failed_stack_delete_keeps_recovery_journal(environment):
    env, clients = environment
    env.journal = {"stack": "test", "token": "token"}
    cf = clients["cloudformation"]
    cf.describe_stacks.return_value = {
        "Stacks": [
            {
                "Tags": [{"Key": "provium-cloud:test-run", "Value": "token"}],
                "StackStatus": "DELETE_FAILED",
            }
        ]
    }
    cf.get_paginator.return_value.paginate.return_value = [
        {"StackResourceSummaries": []}
    ]
    cf.get_waiter.return_value.wait.side_effect = RuntimeError("delete failed")
    with pytest.raises(RuntimeError, match="delete failed"):
        env.cleanup()
    assert not env.journal.get("cleaned")


def test_partial_creation_finishes_before_cleanup_discovers_resources(
    environment, monkeypatch
):
    env, clients = environment
    env.journal = {"stack": "test", "token": "token"}
    tags = [{"Key": "provium-cloud:test-run", "Value": "token"}]
    cf = clients["cloudformation"]
    cf.describe_stacks.side_effect = [
        {"Stacks": [{"Tags": tags, "StackStatus": status}]}
        for status in (
            "CREATE_IN_PROGRESS",
            "ROLLBACK_IN_PROGRESS",
            "ROLLBACK_COMPLETE",
        )
    ]
    cf.get_paginator.return_value.paginate.return_value = [
        {"StackResourceSummaries": []}
    ]
    monkeypatch.setattr("infrastructure.aws.lifecycle.time.sleep", lambda _: None)
    env.cleanup()
    cf.delete_stack.assert_called_once()
    assert env.journal["cleaned"]


def test_collect_downloads_only_known_reports(environment):
    env, clients = environment
    env.journal = {"stack": "test", "token": "token", "passed": True}
    cf = clients["cloudformation"]
    cf.describe_stacks.return_value = {
        "Stacks": [{"Tags": [{"Key": "provium-cloud:test-run", "Value": "token"}]}]
    }
    cf.get_paginator.return_value.paginate.side_effect = [
        [{"StackEvents": []}],
        [
            {
                "StackResourceSummaries": [
                    {
                        "LogicalResourceId": "Artifacts",
                        "PhysicalResourceId": "bucket",
                        "ResourceStatus": "CREATE_COMPLETE",
                    }
                ]
            }
        ],
    ]
    clients["s3"].get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "reports/../../unsafe"}, {"Key": "reports/junit.xml"}]}
    ]

    def download(bucket, key, destination):
        from pathlib import Path

        Path(destination).write_text("<testsuites/>")

    clients["s3"].download_file.side_effect = download
    env.collect()
    clients["s3"].download_file.assert_called_once_with(
        "bucket", "reports/junit.xml", str(env.directory / "junit.xml")
    )


def test_successful_stack_creation_and_resume(environment, monkeypatch):
    env, clients = environment
    cf = clients["cloudformation"]
    cf.create_stack.return_value = {"StackId": "stack-arn"}
    cf.describe_stacks.side_effect = lambda **kw: {
        "Stacks": [
            {
                "Tags": [
                    {"Key": "provium-cloud:test-run", "Value": env.journal["token"]}
                ],
                "Outputs": [{"OutputKey": "Queue", "OutputValue": "queue"}],
            }
        ]
    }
    assert env.create() == {"Queue": "queue"}
    assert env.journal["stack"] == "stack-arn"
    cf.get_waiter.return_value.wait.assert_called_once()
    session = MagicMock()
    session.client.side_effect = lambda name, **kw: clients[name]
    monkeypatch.setattr(
        "infrastructure.aws.lifecycle.boto3.Session", lambda **kw: session
    )
    resumed = Environment.resume(env.directory)
    assert resumed.journal == env.journal
    assert resumed.settings == env.settings


def test_successful_driver_records_completion(environment):
    env, clients = environment
    env.journal = {"token": "run"}
    batch = clients["batch"]
    batch.submit_job.return_value = {"jobId": "driver"}
    batch.describe_jobs.return_value = {"jobs": [{"status": "SUCCEEDED"}]}
    env.execute({"Queue": "q", "Driver": "d"})
    assert env.journal["passed"]
    assert (
        json.loads((env.directory / "driver.json").read_text())["status"] == "SUCCEEDED"
    )


def test_multipart_uploads_are_aborted_before_object_versions(environment):
    env, clients = environment
    s3 = clients["s3"]
    s3.get_paginator.return_value.paginate.side_effect = [
        [{"Uploads": [{"Key": "partial", "UploadId": "upload"}]}],
        [{}],
    ]
    env.empty_bucket("bucket")
    s3.abort_multipart_upload.assert_called_once_with(
        Bucket="bucket", Key="partial", UploadId="upload"
    )
    s3.delete_objects.assert_not_called()
