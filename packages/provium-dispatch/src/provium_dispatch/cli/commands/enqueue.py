"""Enqueue pipeline inputs."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command

from provium_dispatch.cli.arguments import (
    assert_database_is_configured,
    assert_pipeline_is_selected,
    assignments,
    configure_graph,
    configure_queue,
    require_queue,
)


class EnqueueCommand(Command):
    name = "enqueue"
    help = "Enqueue pipeline inputs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)
        configure_queue(parser)
        parser.add_argument(
            "--output", action="append", default=[], metavar="TARGET=PATH"
        )
        parser.add_argument("--csv", help="CSV inputs and per-row output destinations")

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_queue(arguments)
            assert_database_is_configured(arguments)
            assert_pipeline_is_selected(arguments)
            if arguments.csv and (arguments.input or arguments.output):
                raise ValueError(  # noqa: TRY301 -- command reports validation errors
                    "--csv cannot be combined with --input or --output"
                )
            from provium.files import local_file
            from provium_pipeline.requests import CSVSource, ExecutionRequest, InputSet

            if arguments.csv:
                with (
                    local_file(
                        arguments.csv, scratch_directory=arguments.scratch_directory
                    ) as path,
                    path.open(newline="", encoding="utf-8-sig") as stream,
                ):
                    requests = list(CSVSource(stream))
            elif arguments.output:
                requests = [
                    ExecutionRequest(
                        assignments(arguments.input), assignments(arguments.output)
                    )
                ]
            else:
                requests = [InputSet(assignments(arguments.input))]
            from provium_dispatch.persistence.factory import open_backend
            from provium_dispatch.preparation import load_pipeline

            backend = open_backend(arguments.database) if arguments.pipeline else None
            graph = load_pipeline(
                files=arguments.file,
                name=arguments.pipeline,
                backend=backend,
                scratch_directory=arguments.scratch_directory,
            )
            if backend is None:
                backend = open_backend(arguments.database)
            identities: list[int] = []
            for request in requests:
                options = (
                    {"destinations": dict(request.output_destinations)}
                    if request.output_destinations is not None
                    else {}
                )
                identities.append(
                    backend.enqueue(
                        graph.name,
                        dict(request.inputs),
                        queue_group=arguments.queue,
                        **options,
                    )
                )
            print(
                json.dumps(
                    {"ids": identities} if arguments.csv else {"id": identities[0]}
                )
            )
        except Exception as error:
            print(f"provium dispatch: {error}", file=sys.stderr)
            return 2
        return 0
