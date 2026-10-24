"""Locate an intermediate or final cached artifact."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from provium.cli.command import Command

from ..arguments import apply_configuration, assignments, configure_storage

if TYPE_CHECKING:
    from ...lookup import ResultReader
    from ...planning import Plan
    from ...store import ArtifactStore


class LookupCommand(Command):
    name = "lookup"
    group = "pipeline"
    help = "Locate a cached artifact by pipeline output reference and input set"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        self.configure_storage(parser)
        parser.add_argument(
            "reference", help="pipeline.nodes.node.output or pipeline.outputs.output"
        )
        parser.add_argument("-f", "--file", action="append", default=[])
        parser.add_argument("--definition")
        self.configure_inputs(parser)

    def configure_storage(self, parser: argparse.ArgumentParser) -> None:
        configure_storage(parser)

    def prepare(
        self, arguments: argparse.Namespace
    ) -> tuple[Plan, ResultReader | None]:
        from ...preparation import prepare_pipeline

        apply_configuration(arguments)
        if not arguments.file:
            raise ValueError("provide -f for a local pipeline")
        prepared = prepare_pipeline(
            files=arguments.file,
            scratch_directory=arguments.scratch_directory,
            definition_reference=arguments.definition,
        )
        return prepared.plan, None

    def configure_inputs(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--input", action="append", required=True, metavar="NAME=FILE"
        )

    def resolve_inputs(
        self, arguments: argparse.Namespace, store: ArtifactStore
    ) -> dict[str, Any]:
        from ...input_sets import inspect_inputs

        return inspect_inputs(store, assignments(arguments.input))

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            from ...input_sets import input_set_hash
            from ...lookup import lookup_artifact
            from ...store import ArtifactStore

            plan, backend = self.prepare(arguments)
            with ArtifactStore(
                arguments.store, scratch=arguments.scratch_directory
            ) as store:
                inputs = self.resolve_inputs(arguments, store)
                artifact = lookup_artifact(
                    plan, store, inputs, arguments.reference, backend=backend
                )
            if arguments.json:
                print(
                    json.dumps(
                        {
                            "input_set_hash": input_set_hash(inputs),
                            "reference": arguments.reference,
                            "artifact": {
                                "location": artifact.location,
                                **artifact.fingerprint,
                            },
                        }
                    )
                )
            else:
                print(artifact.location)
        except Exception as error:
            print(f"provium {self.group} lookup: {error}", file=sys.stderr)
            return 2
        return 0
