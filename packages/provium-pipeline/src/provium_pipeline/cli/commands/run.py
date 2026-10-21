"""Execute a pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from provium.cli.command import Command

from ..arguments import (
    assignments,
    configure_graph,
    configure_queue,
    require_execution_queue,
    require_pipeline,
)

if TYPE_CHECKING:
    from ...runtime import Outcome


def outcome_json(event: Outcome) -> dict[str, Any]:
    return {
        "state": event.state,
        "node": event.node,
        "reused": event.reused,
        "outputs": {
            name: {"location": artifact.location, **artifact.fingerprint}
            for name, artifact in event.outputs.items()
        },
        "error": event.error,
    }


class RunCommand(Command):
    name = "run"
    help = "Execute a pipeline"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)
        configure_queue(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            self._validate(arguments)
            require_execution_queue(arguments)
            from ...persistence.factory import open_backend
            from ...preparation import prepare_pipeline

            prepared = prepare_pipeline(
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
                    profile=prepared.profile,
                    workers=prepared.overrides,
                    only=arguments.only,
                    force=arguments.force,
                    backend=prepared.backend,
                    queue_group=arguments.queue or "default",
                    idle_wait=arguments.idle_wait,
                ) as context,
            ):
                items = (
                    [] if prepared.backend else [InputSet(assignments(arguments.input))]
                )
                context.start(items)
            print(json.dumps([outcome_json(event) for event in context.outcomes]))
            print(f"Run log: {context.log_path}", file=sys.stderr)
        except Exception as error:
            print(f"provium pipeline: {error}", file=sys.stderr)
            return 2
        return 0

    @staticmethod
    def _validate(arguments: argparse.Namespace) -> None:
        require_pipeline(arguments)
        if arguments.pipeline and arguments.input:
            raise ValueError(
                "registered execution cannot combine files or direct inputs"
            )
