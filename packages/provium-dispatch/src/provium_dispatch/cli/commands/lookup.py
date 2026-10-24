"""Resolve named input sets before locating cached pipeline artifacts."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from provium_pipeline.cli.commands.lookup import LookupCommand as PipelineLookupCommand

from .input_set import open_index

if TYPE_CHECKING:
    from provium_pipeline.store import ArtifactStore


class LookupCommand(PipelineLookupCommand):
    group = "dispatch"

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
