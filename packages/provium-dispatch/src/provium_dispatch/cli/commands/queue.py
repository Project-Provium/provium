"""Summarize queued inputs."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command

from provium_dispatch.cli.arguments import (
    assert_database_is_configured,
    configure_connection,
    configure_queue,
    require_queue,
)


class QueueCommand(Command):
    name = "queue"
    help = "Summarize queued inputs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_connection(parser)
        configure_queue(parser)
        parser.add_argument("pipeline", nargs="?")

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_queue(arguments)
            assert_database_is_configured(arguments)
            from provium_dispatch.persistence.factory import open_backend

            print(
                json.dumps(
                    open_backend(arguments.database).queue_info(
                        arguments.pipeline, queue_group=arguments.queue
                    )
                )
            )
        except Exception as error:
            print(f"provium dispatch: {error}", file=sys.stderr)
            return 2
        return 0
