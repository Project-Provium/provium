"""Remove registered pipelines or profiles."""

from __future__ import annotations

import argparse
import json
import sys

from provium.cli.command import Command

from provium_dispatch.cli.arguments import (
    assert_database_is_configured,
    assert_graph_options_are_valid,
    configure_graph,
)


class UnregisterCommand(Command):
    name = "unregister"
    help = "Remove registered pipelines or profiles"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            self._assert_arguments_are_valid(arguments)
            identities = self._identities(arguments)
            from provium_dispatch.persistence.factory import open_backend

            print(json.dumps(open_backend(arguments.database).unregister(identities)))
        except Exception as error:
            print(f"provium dispatch: {error}", file=sys.stderr)
            return 2
        return 0

    @staticmethod
    def _assert_arguments_are_valid(arguments: argparse.Namespace) -> None:
        assert_database_is_configured(arguments)
        assert_graph_options_are_valid(arguments)
        if arguments.file and (arguments.pipeline or arguments.profile):
            raise ValueError("unregister accepts files or explicit names, not both")
        if not (arguments.file or arguments.pipeline or arguments.profile):
            raise ValueError("unregister requires files, a pipeline name or --profile")

    @staticmethod
    def _identities(arguments: argparse.Namespace) -> list[dict[str, str]]:
        from provium_dispatch.documents import load_documents

        documents = load_documents(
            arguments.file, scratch_directory=arguments.scratch_directory
        )
        identities = [{"kind": doc.kind, "name": doc.name} for doc in documents]
        if arguments.pipeline:
            identities.append(
                {"kind": "PipelineConfiguration", "name": arguments.pipeline}
            )
        if arguments.profile:
            identities.append({"kind": "ComputeProfile", "name": arguments.profile})
        if not identities:
            raise ValueError("unregister requires files, a pipeline name or --profile")
        return identities
