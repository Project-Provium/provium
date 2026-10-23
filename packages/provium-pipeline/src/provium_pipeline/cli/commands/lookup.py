"""Locate an intermediate or final cached artifact."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command

from ..arguments import apply_configuration, assignments
from .input_set import configure_storage, open_index


class LookupCommand(Command):
    name = "lookup"
    help = "Locate a cached artifact by pipeline output reference and input set"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_storage(parser)
        parser.add_argument(
            "reference", help="pipeline.nodes.node.output or pipeline.outputs.output"
        )
        parser.add_argument("-f", "--file", action="append", default=[])
        parser.add_argument("--definition")
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument(
            "--input", action="append", default=[], metavar="NAME=FILE"
        )
        selection.add_argument("--input-set", help="Registered input-set name")
        selection.add_argument(
            "--input-set-hash", help="Previously indexed input-set hash"
        )

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            from ...input_sets import input_set_hash, inspect_inputs
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
                if arguments.input:
                    inputs = inspect_inputs(store, assignments(arguments.input))
                else:
                    index = backend or open_index(arguments, store)
                    record = index.resolve_input_set(
                        name=arguments.input_set, key=arguments.input_set_hash
                    )
                    inputs = record["inputs"]
                    if input_set_hash(inputs) != record["hash"]:
                        raise ValueError("input-set index fingerprint mismatch")  # noqa: TRY301
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
            print(f"provium pipeline lookup: {error}", file=sys.stderr)
            return 2
        return 0
