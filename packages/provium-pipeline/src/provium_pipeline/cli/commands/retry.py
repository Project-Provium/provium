"""Reset failed queued inputs."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command

from ..arguments import configure_graph, require_database, require_pipeline


class RetryCommand(Command):
    name = "retry"
    help = "Reset failed queued inputs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_database(arguments)
            require_pipeline(arguments)
            from ...persistence.factory import open_backend
            from ...preparation import load_pipeline

            backend = open_backend(arguments.database) if arguments.pipeline else None
            graph = load_pipeline(
                files=arguments.file,
                name=arguments.pipeline,
                backend=backend,
                scratch_directory=arguments.scratch_directory,
            )
            if backend is None:
                backend = open_backend(arguments.database)
            print(json.dumps({"reset": backend.retry(graph.name)}))
        except Exception as error:
            print(f"provium pipeline: {error}", file=sys.stderr)
            return 2
        return 0
