"""Query and manage durable run history."""

import argparse
from typing import Any

from provium_cloud.cli.aws.arguments import configure_deployment, deployment
from provium_cloud.cli.aws.commands.base import AWSAction
from provium_cloud.client import CloudClient
from provium_cloud.state.models import Run, RunPage


class ListCommand(AWSAction):
    name = "list"
    help = "List input sets and run progress"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_deployment(parser)
        parser.add_argument(
            "--status",
            action="append",
            choices=["queued", "running", "succeeded", "failed", "canceled"],
        )
        parser.add_argument("--input-set-id")
        parser.add_argument("--cursor")
        parser.add_argument("--limit", type=int, default=100)

    def action(self, arguments: argparse.Namespace) -> RunPage:
        with CloudClient(deployment(arguments)) as client:
            return client.list_runs(
                statuses=arguments.status,
                input_set_id=arguments.input_set_id,
                cursor=arguments.cursor,
                limit=arguments.limit,
            )


class StatusCommand(AWSAction):
    name = "status"
    help = "Show run inputs, tasks, progress, status, and outputs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_deployment(parser)
        parser.add_argument("run_id")

    def action(self, arguments: argparse.Namespace) -> Run:
        with CloudClient(deployment(arguments)) as client:
            return client.get_run(arguments.run_id)


class InspectCommand(StatusCommand):
    name = "inspect"
    help = "Inspect a run or a task’s shared computation attempts"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        super().configure(parser)
        parser.add_argument("--node")

    def action(self, arguments: argparse.Namespace) -> Any:
        with CloudClient(deployment(arguments)) as client:
            return (
                client.attempts(arguments.run_id, arguments.node)
                if arguments.node
                else client.get_run(arguments.run_id)
            )


class CancelCommand(StatusCommand):
    name = "cancel"
    help = "Cancel a run; preserve work still needed by other runs"

    def action(self, arguments: argparse.Namespace) -> Run:
        with CloudClient(deployment(arguments)) as client:
            return client.cancel(arguments.run_id)


class RetryCommand(StatusCommand):
    name = "retry"
    help = "Retry unfinished tasks of a failed or canceled run"

    def action(self, arguments: argparse.Namespace) -> Run:
        with CloudClient(deployment(arguments)) as client:
            return client.retry(arguments.run_id)


class WaitCommand(StatusCommand):
    name = "wait"
    help = "Wait for a run to finish; exit nonzero if it fails or is canceled"
    fail_on_run_error = True

    def configure(self, parser: argparse.ArgumentParser) -> None:
        super().configure(parser)
        parser.add_argument("--timeout", type=float)
        parser.add_argument("--interval", type=float, default=2)

    def action(self, arguments: argparse.Namespace) -> Run:
        with CloudClient(deployment(arguments)) as client:
            return client.wait(
                arguments.run_id, timeout=arguments.timeout, interval=arguments.interval
            )
