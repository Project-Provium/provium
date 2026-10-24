"""Inspect pipeline execution status."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command

from ..arguments import (
    assignments,
    configure_graph,
    require_pipeline,
)


class StatusCommand(Command):
    name = "status"
    help = "Inspect pipeline execution status"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_pipeline(arguments)
            from ...preparation import prepare_pipeline

            prepared = prepare_pipeline(
                files=arguments.file,
                scratch_directory=arguments.scratch_directory,
                definition_reference=arguments.definition,
                workers=[
                    {key: int(count)}
                    for value in arguments.workers
                    for key, _, count in [value.partition("=")]
                ],
                only=arguments.only,
                force=arguments.force,
            )
            from ...runtime import ExecutionContext, InputSet
            from ...store import ArtifactStore

            store = ArtifactStore(arguments.store, scratch=arguments.scratch_directory)
            with (
                store,
                ExecutionContext(
                    prepared.plan.configuration,
                    store,
                    definition=prepared.definition,
                    workers=prepared.overrides,
                    only=arguments.only,
                    force=arguments.force,
                    idle_wait=arguments.idle_wait,
                ) as context,
            ):
                result = context.inspect_inputs(
                    [InputSet(assignments(arguments.input))]
                )
                print(json.dumps(result))
        except Exception as error:
            print(f"provium pipeline: {error}", file=sys.stderr)
            return 2
        return 0
