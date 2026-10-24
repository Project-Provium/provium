"""Resolve named input sets before locating cached pipeline artifacts."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from provium_pipeline.cli.commands.lookup import LookupCommand as PipelineLookupCommand

from .input_set import open_index

if TYPE_CHECKING:
    from provium_pipeline.lookup import ResultReader
    from provium_pipeline.planning import Plan
    from provium_pipeline.store import ArtifactStore


class LookupCommand(PipelineLookupCommand):
    group = "dispatch"

    def configure_storage(self, parser: argparse.ArgumentParser) -> None:
        from ..arguments import configure_storage

        configure_storage(parser)

    def prepare(
        self, arguments: argparse.Namespace
    ) -> tuple[Plan, ResultReader | None]:
        from provium_pipeline.lookup import split_selector

        from ...persistence.factory import open_backend
        from ...preparation import prepare_pipeline
        from ..arguments import apply_configuration

        apply_configuration(arguments)
        pipeline, _ = split_selector(arguments.reference)
        backend = None
        if not arguments.file:
            if not arguments.database:
                raise ValueError(
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
        return prepared.plan, backend

    def configure_inputs(self, parser: argparse.ArgumentParser) -> None:
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument(
            "--input", action="append", default=[], metavar="NAME=FILE"
        )
        selection.add_argument("--input-set", help="Registered input-set name")
        selection.add_argument(
            "--input-set-hash", help="Previously indexed input-set hash"
        )

    def resolve_inputs(
        self, arguments: argparse.Namespace, store: ArtifactStore
    ) -> dict[str, Any]:
        from provium_pipeline.input_sets import input_set_hash

        if arguments.input:
            return super().resolve_inputs(arguments, store)
        record = open_index(arguments, store).resolve_input_set(
            name=arguments.input_set, key=arguments.input_set_hash
        )
        inputs = record["inputs"]
        if input_set_hash(inputs) != record["hash"]:
            raise ValueError("input-set index fingerprint mismatch")
        return inputs
