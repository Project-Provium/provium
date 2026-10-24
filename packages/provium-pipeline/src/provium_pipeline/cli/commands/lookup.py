"""Locate an intermediate or final cached artifact."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from provium.cli.command import Command

from ..arguments import apply_configuration, assignments, configure_storage

if TYPE_CHECKING:
    from ...store import ArtifactStore


class LookupCommand(Command):
    name = "lookup"
    group = "pipeline"
    help = "Locate a cached artifact by pipeline output reference and input set"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_storage(parser)
        parser.add_argument(
            "reference", help="pipeline.nodes.node.output or pipeline.outputs.output"
        )
        parser.add_argument("-f", "--file", action="append", default=[])
        parser.add_argument("--definition")
        self.configure_inputs(parser)

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
            from ...lookup import lookup_artifact, split_selector
            from ...persistence.factory import open_backend
            from ...preparation import prepare_pipeline
            from ...store import ArtifactStore

            apply_configuration(arguments)
            pipeline, _ = split_selector(arguments.reference)
            backend = None
            if not arguments.file:
                if not arguments.database:
                    raise ValueError(  # noqa: TRY301
                        "provide -f for a local pipeline or --database "
                        "for a registered pipeline"
                    )
                backend = open_backend(arguments.database)
            prepared = prepare_pipeline(
                files=arguments.file,
                name=None if arguments.file else pipeline,
                backend=backend,
                scratch_directory=arguments.scratch_directory,
                definition_reference=arguments.definition,
            )
            with ArtifactStore(
                arguments.store, scratch=arguments.scratch_directory
            ) as store:
                inputs = self.resolve_inputs(arguments, store)
                artifact = lookup_artifact(
                    prepared.plan, store, inputs, arguments.reference, backend=backend
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
