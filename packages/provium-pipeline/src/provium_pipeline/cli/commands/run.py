"""Execute a pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from provium.cli.command import Command

from provium_pipeline.cli.arguments import (
    assert_pipeline_is_selected,
    assignments,
    configure_graph,
)

if TYPE_CHECKING:
    from provium_pipeline.runtime import Outcome


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
        parser.add_argument(
            "--discard-cache",
            action="store_true",
            help="Remove this run's cache after successful output publication",
        )
        parser.add_argument(
            "--output", action="append", default=[], metavar="TARGET=PATH"
        )
        parser.add_argument(
            "--csv", help="CSV requests with inputs.NAME and target/output columns"
        )
        parser.add_argument(
            "--strategy",
            choices=("item", "stage"),
            default="stage",
            help="Complete each input in turn or reuse loaded procedures across inputs",
        )
        parser.add_argument(
            "--verify-artifacts",
            action="store_true",
            help="Read and verify cached artifact contents before reusing them",
        )
        parser.add_argument(
            "--verbose",
            "-v",
            action="store_true",
            help="Report procedures skipped because cached outputs were reused",
        )

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            self._assert_arguments_are_valid(arguments)
            from provium_pipeline.preparation import prepare_pipeline

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
            from provium.files import local_file

            from provium_pipeline.requests import CSVSource, ExecutionRequest
            from provium_pipeline.runtime import ExecutionContext
            from provium_pipeline.store import ArtifactStore

            if arguments.csv:
                with (
                    local_file(
                        arguments.csv, scratch_directory=arguments.scratch_directory
                    ) as path,
                    path.open(newline="", encoding="utf-8-sig") as stream,
                ):
                    items = list(CSVSource(stream))
            else:
                items = [
                    ExecutionRequest(
                        assignments(arguments.input), assignments(arguments.output)
                    )
                ]
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
                    verify_artifacts=arguments.verify_artifacts,
                    strategy=arguments.strategy,
                ) as context,
            ):

                def report_step(event: Outcome) -> None:
                    if event.reused:
                        print(
                            f"Skipped {event.node}: reused cached outputs",
                            file=sys.stderr,
                            flush=True,
                        )

                context.start(
                    items,
                    on_step_finished=report_step if arguments.verbose else None,
                )
            if arguments.discard_cache:
                from provium_pipeline.cache import discard_published_cache

                discard_published_cache(
                    store, context.outcomes, context.computation_keys
                )
            if arguments.json:
                print(json.dumps([outcome_json(event) for event in context.outcomes]))
            else:
                if not context.outcomes:
                    print("No input sets processed.")
                for index, event in enumerate(context.outcomes, start=1):
                    print(f"Input set {index}: {event.state}")
                    for name, artifact in event.outputs.items():
                        print(f"  {name}: {artifact.location}")
                    if event.error:
                        print(f"  Error: {event.error.strip().splitlines()[-1]}")
        except Exception as error:
            print(f"provium pipeline: {error}", file=sys.stderr)
            return 2
        return 1 if any(event.state == "failed" for event in context.outcomes) else 0

    @staticmethod
    def _assert_arguments_are_valid(arguments: argparse.Namespace) -> None:
        assert_pipeline_is_selected(arguments)
        if arguments.csv and (arguments.input or arguments.output):
            raise ValueError("--csv cannot be combined with --input or --output")
        if not arguments.csv and not arguments.output:
            raise ValueError(
                "execution requires at least one --output TARGET=PATH or --csv"
            )
