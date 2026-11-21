"""Look up committed computation outputs and retrieve run artifacts."""

import argparse
from typing import Any

from provium_cloud.cli.aws.arguments import configure_deployment, deployment
from provium_cloud.cli.aws.commands.base import AWSAction
from provium_cloud.client import CloudClient


class LookupCommand(AWSAction):
    name = "lookup"
    help = "Look up verified outputs by computation key"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_deployment(parser)
        parser.add_argument("computation")

    def action(self, arguments: argparse.Namespace) -> Any:
        with CloudClient(deployment(arguments)) as client:
            return {"outputs": client.lookup(arguments.computation)}


class FetchCommand(AWSAction):
    name = "fetch"
    help = "Download a committed run output to a local artifact file"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_deployment(parser)
        parser.add_argument("run_id")
        parser.add_argument("output")
        parser.add_argument("destination")
        parser.add_argument("--overwrite", action="store_true")

    def action(self, arguments: argparse.Namespace) -> dict[str, str]:
        with CloudClient(deployment(arguments)) as client:
            client.fetch(
                arguments.run_id,
                arguments.output,
                arguments.destination,
                overwrite=arguments.overwrite,
            )
        return {"destination": arguments.destination}
