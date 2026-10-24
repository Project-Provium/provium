"""Inspect pipeline execution status."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from provium.cli.command import Command

from ..arguments import (
    assignments,
    configure_graph,
    configure_queue,
    require_execution_queue,
    require_pipeline,
)


class StatusCommand(Command):
    name = "status"
    help = "Inspect pipeline execution status"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)
        configure_queue(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            require_pipeline(arguments)
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
            from provium_pipeline.requests import InputSet
            from provium_pipeline.store import ArtifactStore

            from ...runtime import ExecutionContext

            graph = prepared.plan.configuration
            backend = prepared.backend
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
                if backend and not arguments.input:
                    rows: list[dict[str, Any]] = []
                    after = 0
                    while batch := backend.pending(
                        graph.name,
                        after,
                        include_finished=True,
                        queue_group=arguments.queue,
                    ):
                        rows.extend(batch)
                        after = batch[-1]["id"]
                    if arguments.outputs:
                        result: list[dict[str, Any]] = []
                        for row in rows:
                            recorded_store = row["detail"].get("store", store.namespace)
                            with ArtifactStore(
                                recorded_store, scratch=arguments.scratch_directory
                            ) as reader:
                                with ExecutionContext(
                                    graph, reader, backend=backend, only=arguments.only
                                ) as inspector:
                                    report = inspector.inspect_inputs(
                                        [InputSet(row["inputs"])]
                                    )[0]
                                    report["id"] = row["id"]
                                    result.append(report)
                    else:
                        result = [
                            {"id": row["id"], "state": row["state"], **row["detail"]}
                            for row in rows
                        ]
                else:
                    result = context.inspect_inputs(
                        [InputSet(assignments(arguments.input))]
                    )
                print(json.dumps(result))
        except Exception as error:
            print(f"provium dispatch: {error}", file=sys.stderr)
            return 2
        return 0
