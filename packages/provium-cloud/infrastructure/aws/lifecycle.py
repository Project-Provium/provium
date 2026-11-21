"""Owned-stack lifecycle, recoverable cleanup, and local test evidence."""

import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import Settings
from .image import build_image

ACTIVE = ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING")
HERE = Path(__file__).parent


class Environment:
    def __init__(self, settings: Settings, directory: Path, *, session: Any = None):
        self.settings = settings
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        session = session or boto3.Session(region_name=settings.region)
        config = Config(
            retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=30
        )
        self.clients = {
            name: session.client(name, region_name=settings.region, config=config)
            for name in ("sts", "cloudformation", "batch", "s3", "logs", "ecr")
        }
        self.journal: dict[str, Any] = {}

    @classmethod
    def resume(cls, directory: Path) -> "Environment":
        journal = json.loads((directory / "state.json").read_text())
        result = cls(Settings(**journal["settings"]), directory)
        result.journal = journal
        return result

    def save(self) -> None:
        path = self.directory / "state.json.tmp"
        path.touch(mode=0o600)
        path.write_text(json.dumps(self.journal, indent=2) + "\n")
        path.replace(self.directory / "state.json")

    def check_account(self) -> None:
        account = self.clients["sts"].get_caller_identity()["Account"]
        if account != self.settings.account_id:
            raise ValueError(
                "AWS credentials do not match the configured test account."
            )

    def create(self) -> dict[str, str]:
        self.check_account()
        if (self.directory / "state.json").exists():
            raise ValueError(
                "Results directory already has a run. Use cleanup or a new directory."
            )
        token = uuid.uuid4().hex
        self.journal = {
            "settings": asdict(self.settings),
            "token": token,
            "stack": f"{self.settings.stack_prefix}-{token[:12]}",
        }
        self.save()
        cf = self.clients["cloudformation"]
        template = (HERE / "stack.yaml").read_text()
        cf.validate_template(TemplateBody=template)
        print(f"Creating {self.journal['stack']}", flush=True)
        response = cf.create_stack(
            StackName=self.journal["stack"],
            TemplateBody=template,
            RoleARN=self.settings.role_arn,
            Capabilities=["CAPABILITY_IAM"],
            Parameters=[
                {
                    "ParameterKey": "TestTimeout",
                    "ParameterValue": str(self.settings.test_timeout),
                }
            ],
            Tags=[{"Key": "provium-cloud:test-run", "Value": token}],
            ClientRequestToken=token,
            OnFailure="ROLLBACK",
        )
        self.journal["stack"] = response["StackId"]
        self.save()
        cf.get_waiter("stack_create_complete").wait(
            StackName=self.journal["stack"],
            WaiterConfig={"Delay": 20, "MaxAttempts": 90},
        )
        stack = self.owned_stack()
        assert stack is not None
        outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
        self.journal["outputs"] = outputs
        self.save()
        return outputs

    def owned_stack(self) -> dict[str, Any] | None:
        try:
            stacks = self.clients["cloudformation"].describe_stacks(
                StackName=self.journal["stack"]
            )["Stacks"]
        except ClientError as error:
            detail = error.response.get("Error", {})
            if detail.get(
                "Code"
            ) == "ValidationError" and "does not exist" in detail.get("Message", ""):
                return None
            raise
        stack = stacks[0]
        tags = {tag["Key"]: tag["Value"] for tag in stack.get("Tags", [])}
        if tags.get("provium-cloud:test-run") != self.journal["token"]:
            raise ValueError(
                "Stack ownership does not match the run journal; refusing changes."
            )
        return stack

    def run(self) -> None:
        errors: list[BaseException] = []
        try:
            outputs = self.create()
            print("Building and pushing test image (see image.log)", flush=True)
            build_image(self.clients["ecr"], outputs["RepositoryUri"], self.directory)
            self.execute(outputs)
        except BaseException as error:
            errors.append(error)
        finally:
            # Collection must never prevent teardown, even after a failed submission.
            for action in (self.collect, self.cleanup):
                try:
                    action()
                except BaseException as error:
                    errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("AWS tests or cleanup failed", errors)

    def execute(self, outputs: dict[str, str]) -> None:
        batch = self.clients["batch"]
        print("Running live tests on AWS Batch", flush=True)
        response = batch.submit_job(
            jobName=f"provium-test-{self.journal['token']}",
            jobQueue=outputs["Queue"],
            jobDefinition=outputs["Driver"],
        )
        self.journal["job"] = response["jobId"]
        self.save()
        deadline = time.monotonic() + self.settings.test_timeout + 300
        while time.monotonic() < deadline:
            jobs = batch.describe_jobs(jobs=[self.journal["job"]])["jobs"]
            if jobs and jobs[0]["status"] in ("SUCCEEDED", "FAILED"):
                job = jobs[0]
                (self.directory / "driver.json").write_text(json.dumps(job, indent=2))
                if job["status"] != "SUCCEEDED":
                    raise RuntimeError(
                        f"Live test job failed: {job.get('statusReason', 'see logs')}"
                    )
                self.journal["passed"] = True
                self.save()
                return
            time.sleep(10)
        raise TimeoutError(
            "Live tests exceeded their deadline; terminating during teardown."
        )

    def resources(self) -> dict[str, str]:
        pages = (
            self.clients["cloudformation"]
            .get_paginator("list_stack_resources")
            .paginate(StackName=self.journal["stack"])
        )
        return {
            item["LogicalResourceId"]: item["PhysicalResourceId"]
            for page in pages
            for item in page["StackResourceSummaries"]
            if item.get("PhysicalResourceId")
            and item["ResourceStatus"] != "DELETE_COMPLETE"
        }

    def collect(self) -> None:
        if not self.journal or self.journal.get("cleaned"):
            return
        self.check_account()
        if self.owned_stack() is None:
            return
        cf = self.clients["cloudformation"]
        pages = cf.get_paginator("describe_stack_events").paginate(
            StackName=self.journal["stack"]
        )
        with (self.directory / "stack-events.jsonl").open("w") as file:
            for page in pages:
                for event in page["StackEvents"]:
                    file.write(json.dumps(event, default=str) + "\n")
        resources = self.resources()
        if "Logs" in resources:
            pages = (
                self.clients["logs"]
                .get_paginator("filter_log_events")
                .paginate(logGroupName=resources["Logs"])
            )
            with (self.directory / "batch-logs.jsonl").open("w") as file:
                for page in pages:
                    for event in page["events"]:
                        file.write(json.dumps(event) + "\n")
        if "Artifacts" in resources:
            s3 = self.clients["s3"]
            pages = s3.get_paginator("list_objects_v2").paginate(
                Bucket=resources["Artifacts"], Prefix="reports/"
            )
            for page in pages:
                for item in page.get("Contents", []):
                    if item["Key"] in ("reports/junit.xml", "reports/summary.json"):
                        s3.download_file(
                            resources["Artifacts"],
                            item["Key"],
                            str(self.directory / Path(item["Key"]).name),
                        )
        if self.journal.get("passed") and not (self.directory / "junit.xml").is_file():
            raise RuntimeError("Driver succeeded but its JUnit report is missing.")

    def stop_jobs(self, queue: str) -> None:
        batch = self.clients["batch"]
        batch.update_job_queue(jobQueue=queue, state="DISABLED")
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            jobs = {}
            for status in ACTIVE:
                pages = batch.get_paginator("list_jobs").paginate(
                    jobQueue=queue, jobStatus=status
                )
                for page in pages:
                    for job in page["jobSummaryList"]:
                        jobs[job["jobId"]] = status
            if not jobs:
                return
            for identity in jobs:
                # TerminateJob also cancels queued jobs, handling status races.
                batch.terminate_job(
                    jobId=identity, reason="Disposable test environment teardown"
                )
            time.sleep(10)
        raise TimeoutError("Batch jobs still active; refusing to remove their storage.")

    def empty_bucket(self, bucket: str) -> None:
        s3 = self.clients["s3"]
        pages = s3.get_paginator("list_multipart_uploads").paginate(Bucket=bucket)
        for page in pages:
            for upload in page.get("Uploads", []):
                s3.abort_multipart_upload(
                    Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
                )
        pages = s3.get_paginator("list_object_versions").paginate(Bucket=bucket)
        for page in pages:
            objects = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in page.get("Versions", []) + page.get("DeleteMarkers", [])
            ]
            if objects:
                response = s3.delete_objects(
                    Bucket=bucket, Delete={"Objects": objects, "Quiet": True}
                )
                if response.get("Errors"):
                    raise RuntimeError(
                        "Could not empty test bucket; check S3 permissions and retry."
                    )

    def cleanup(self) -> None:
        if not self.journal or self.journal.get("cleaned"):
            return
        self.check_account()
        cf = self.clients["cloudformation"]
        stack = self.owned_stack()
        # A partially created/rolling-back stack may still be adding resources.
        deadline = time.monotonic() + 1800
        while stack and stack["StackStatus"].endswith("_IN_PROGRESS"):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Stack is still changing; rerun cleanup using state.json."
                )
            time.sleep(20)
            stack = self.owned_stack()
        if stack and stack["StackStatus"] != "DELETE_COMPLETE":
            print(f"Deleting {self.journal['stack']}", flush=True)
            resources = self.resources()
            if "Queue" in resources:
                self.stop_jobs(resources["Queue"])
            if "Artifacts" in resources:
                self.empty_bucket(resources["Artifacts"])
            cf.delete_stack(
                StackName=self.journal["stack"], RoleARN=self.settings.role_arn
            )
            cf.get_waiter("stack_delete_complete").wait(
                StackName=self.journal["stack"],
                WaiterConfig={"Delay": 20, "MaxAttempts": 120},
            )
        self.journal["cleaned"] = True
        self.save()
