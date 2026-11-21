"""Controller, worker, and provider log commands."""

import argparse
import os
from typing import Any

from provium_cloud.cli.aws.arguments import configure_deployment, deployment
from provium_cloud.cli.aws.commands.base import AWSAction
from provium_cloud.client import CloudClient
from provium_cloud.execution.aws.batch import BatchExecutor
from provium_cloud.execution.controller import Controller


class ControllerCommand(AWSAction):
    name = "controller"
    help = "Advance queued runs and reconcile AWS Batch jobs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_deployment(parser)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=2)

    def action(self, arguments: argparse.Namespace) -> None:
        with CloudClient(deployment(arguments)) as client:
            controller = Controller(client)
            if arguments.once:
                controller.tick()
            else:
                controller.serve(interval=arguments.interval)


class WorkerCommand(AWSAction):
    name = "worker"
    help = "Execute an admitted computation inside a configured Batch job"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_deployment(parser, worker=True)
        parser.add_argument("--computation", required=True)
        parser.add_argument("--generation", required=True, type=int)
        parser.add_argument("--attempt", required=True)

    def action(self, arguments: argparse.Namespace) -> dict[str, bool]:
        from provium_cloud.execution.worker import execute_job

        return {
            "executed": execute_job(
                deployment(arguments),
                arguments.computation,
                arguments.generation,
                arguments.attempt,
                job_id=os.environ.get("AWS_BATCH_JOB_ID"),
            )
        }


class LogsCommand(AWSAction):
    name = "logs"
    help = "Read an AWS Batch job’s CloudWatch log page"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_deployment(parser)
        parser.add_argument("job_id")
        parser.add_argument("--cursor")
        parser.add_argument("--limit", type=int, default=100)

    def action(self, arguments: argparse.Namespace) -> dict[str, Any]:
        return BatchExecutor(deployment(arguments).region).logs(
            arguments.job_id, cursor=arguments.cursor, limit=arguments.limit
        )
