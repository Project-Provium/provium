"""Plan and enqueue unnamed pipeline or standalone procedure invocations."""

import argparse

from provium_cloud.cli.aws.arguments import (
    assignments,
    configure_invocation,
    deployment,
    invocation,
)
from provium_cloud.cli.aws.commands.base import AWSAction
from provium_cloud.client import CloudClient
from provium_cloud.invocations.models import InvocationPlan
from provium_cloud.state.models import Run


class PlanCommand(AWSAction):
    name = "plan"
    help = "Validate and resolve a graph and scheduling routes without submitting work"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_invocation(parser)

    def action(self, arguments: argparse.Namespace) -> InvocationPlan:
        return invocation(arguments, deployment(arguments))


class EnqueueCommand(AWSAction):
    name = "enqueue"
    help = "Durably enqueue a procedure or pipeline input set"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_invocation(parser)
        parser.add_argument("--request-id", help="Idempotency key for this submission")

    def action(self, arguments: argparse.Namespace) -> Run:
        config = deployment(arguments)
        plan = invocation(arguments, config)
        with CloudClient(config) as client:
            return client.enqueue(
                plan, assignments(arguments.input), request_id=arguments.request_id
            )
