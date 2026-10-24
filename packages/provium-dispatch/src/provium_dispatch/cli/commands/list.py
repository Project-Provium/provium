"""List registered pipelines or profiles."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command
from provium_pipeline.cli.arguments import configure_connection, require_database


class ListCommand(Command):
    name = "list"
    help = "List registered pipelines or profiles"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_connection(parser)
        parser.add_argument(
            "resource", nargs="?", choices=("pipelines", "profiles"), default=None
        )

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_database(arguments)
            from provium_pipeline.persistence.factory import open_backend

            backend = open_backend(arguments.database)
            kind = {
                "pipelines": "PipelineConfiguration",
                "profiles": "ComputeProfile",
            }.get(arguments.resource)
            print(json.dumps(backend.registered(kind)))
        except Exception as error:
            print(f"provium dispatch: {error}", file=sys.stderr)
            return 2
        return 0
