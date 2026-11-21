"""AWS Batch API adapter with submission reconciliation by immutable attempt ID."""

from importlib import import_module
from typing import Any

from provium_cloud.deployment import CloudDeployment
from provium_cloud.state.models import Generation


class BatchExecutor:
    def __init__(
        self, region: str, *, client: Any = None, logs_client: Any = None
    ) -> None:
        self.region = region
        self._logs = logs_client
        if client is None:
            boto3 = import_module("boto3")
            config = import_module("botocore.config").Config
            # SubmitJob has no idempotency token; reconcile before application retries.
            client = boto3.client(
                "batch",
                region_name=region,
                config=config(
                    retries={"total_max_attempts": 1},
                    connect_timeout=10,
                    read_timeout=30,
                ),
            )
        self.client = client

    def submit(
        self, key: str, generation: Generation, deployment: CloudDeployment
    ) -> str:
        arguments: dict[str, Any] = {
            "jobName": f"provium-{generation.attempt}",
            "jobQueue": generation.settings.job_queue,
            "jobDefinition": generation.settings.job_definition,
            "retryStrategy": {"attempts": 1},
            "tags": {"provium:computation": key, "provium:attempt": generation.attempt},
            "containerOverrides": {
                "command": [
                    "provium",
                    "cloud",
                    "aws",
                    "worker",
                    "--computation",
                    key,
                    "--generation",
                    str(generation.number),
                    "--attempt",
                    generation.attempt,
                ],
                "environment": [
                    {
                        "name": "PROVIUM_CLOUD_DEPLOYMENT",
                        "value": deployment.model_dump_json(),
                    }
                ],
            },
        }
        if generation.settings.timeout is not None:
            arguments["timeout"] = {
                "attemptDurationSeconds": generation.settings.timeout
            }
        return str(self.client.submit_job(**arguments)["jobId"])

    def find(self, generation: Generation) -> list[str]:
        pages = self.client.get_paginator("list_jobs").paginate(
            jobQueue=generation.settings.job_queue,
            filters=[{"name": "JOB_NAME", "values": [f"provium-{generation.attempt}"]}],
        )
        return [
            str(job["jobId"])
            for page in pages
            for job in page["jobSummaryList"]
            if job["jobName"] == f"provium-{generation.attempt}"
        ]

    def describe(self, jobs: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for offset in range(0, len(jobs), 100):
            response = self.client.describe_jobs(jobs=jobs[offset : offset + 100])
            result.update(
                {str(job["jobId"]): str(job["status"]) for job in response["jobs"]}
            )
        return result

    def cancel(self, job: str, status: str, reason: str) -> None:
        if status in ("SUCCEEDED", "FAILED"):
            return
        method = (
            self.client.terminate_job
            if status in ("STARTING", "RUNNING")
            else self.client.cancel_job
        )
        method(jobId=job, reason=reason[:1024])

    def logs(
        self, job_id: str, *, cursor: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        if not 1 <= limit <= 10000:
            raise ValueError("log limit must be between 1 and 10000")
        jobs = self.client.describe_jobs(jobs=[job_id])["jobs"]
        if not jobs:
            raise LookupError(f"unknown AWS Batch job: {job_id}")
        container = jobs[0].get("container", {})
        stream = container.get("logStreamName")
        if not stream:
            return {"events": [], "next_cursor": None}
        if self._logs is None:
            self._logs = import_module("boto3").client("logs", region_name=self.region)
        arguments: dict[str, Any] = {
            "logGroupName": container.get("logConfiguration", {})
            .get("options", {})
            .get("awslogs-group", "/aws/batch/job"),
            "logStreamName": stream,
            "startFromHead": True,
            "limit": limit,
        }
        if cursor:
            arguments["nextToken"] = cursor
        response = self._logs.get_log_events(**arguments)
        return {
            "events": response["events"],
            "next_cursor": response.get("nextForwardToken"),
        }
