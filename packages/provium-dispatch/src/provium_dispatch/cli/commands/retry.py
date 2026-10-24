"""Reset failed queued inputs."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command
from provium_pipeline.cli.arguments import (
    configure_graph,
    configure_queue,
    require_database,
    require_pipeline,
    require_queue,
)


class RetryCommand(Command):
    name = "retry"
    help = "Reset failed queued inputs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)
        configure_queue(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_queue(arguments)
            require_database(arguments)
            require_pipeline(arguments)
            from provium_pipeline.persistence.factory import open_backend
            from provium_pipeline.preparation import load_pipeline

            backend = open_backend(arguments.database) if arguments.pipeline else None
            graph = load_pipeline(
                files=arguments.file,
                name=arguments.pipeline,
                backend=backend,
                scratch_directory=arguments.scratch_directory,
            )
            if backend is None:
                backend = open_backend(arguments.database)
            print(
                json.dumps(
                    {"reset": backend.retry(graph.name, queue_group=arguments.queue)}
                )
            )
        except Exception as error:
            print(f"provium dispatch: {error}", file=sys.stderr)
            return 2
        return 0
