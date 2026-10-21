"""Summarize queued inputs."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command

from ..arguments import configure_connection, require_database


class QueueCommand(Command):
    name = "queue"
    help = "Summarize queued inputs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_connection(parser)
        parser.add_argument("pipeline", nargs="?")

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_database(arguments)
            from ...persistence.factory import open_backend

            print(
                json.dumps(
                    open_backend(arguments.database).queue_info(arguments.pipeline)
                )
            )
        except Exception as error:
            print(f"provium pipeline: {error}", file=sys.stderr)
            return 2
        return 0
