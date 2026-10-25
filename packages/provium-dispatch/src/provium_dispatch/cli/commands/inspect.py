"""Inspect a pipeline plan."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command

from provium_dispatch.cli.arguments import configure_graph, require_pipeline


class InspectCommand(Command):
    name = "inspect"
    help = "Inspect a pipeline plan"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_pipeline(arguments)
            from provium_dispatch.persistence.factory import open_backend
            from provium_dispatch.preparation import prepare_pipeline

            print(
                json.dumps(
                    prepare_pipeline(
                        files=arguments.file,
                        name=arguments.pipeline,
                        backend=open_backend(arguments.database)
                        if arguments.pipeline
                        else None,
                        scratch_directory=arguments.scratch_directory,
                        definition_reference=arguments.definition,
                        profile_name=arguments.profile,
                        workers=[
                            {key: int(count)}
                            for value in arguments.workers
                            for key, _, count in [value.partition("=")]
                        ],
                        only=arguments.only,
                        force=arguments.force,
                    ).summary()
                )
            )
        except Exception as error:
            print(f"provium dispatch: {error}", file=sys.stderr)
            return 2
        return 0
