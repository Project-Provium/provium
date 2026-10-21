"""Enqueue pipeline inputs."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command

from ..arguments import assignments, configure_graph, require_database, require_pipeline


class EnqueueCommand(Command):
    name = "enqueue"
    help = "Enqueue pipeline inputs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_database(arguments)
            require_pipeline(arguments)
            values = assignments(arguments.input)
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
            print(json.dumps({"id": backend.enqueue(graph.name, values)}))
        except Exception as error:
            print(f"provium pipeline: {error}", file=sys.stderr)
            return 2
        return 0
