"""Register pipeline and profile documents."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from provium.cli.command import Command

if TYPE_CHECKING:
    from provium_pipeline.models import PipelineConfiguration

    from provium_dispatch.models import ComputeProfile

from provium_dispatch.cli.arguments import (
    assert_database_is_configured,
    assert_graph_options_are_valid,
    configure_graph,
)


class RegisterCommand(Command):
    name = "register"
    help = "Register pipeline and profile documents"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            self._assert_arguments_are_valid(arguments)
            from provium_pipeline.definitions import load_definition
            from provium_pipeline.models import (
                PipelineConfiguration,
                PipelineDefinition,
            )
            from provium_pipeline.planning import build_plan

            from provium_dispatch.persistence.factory import open_backend

            documents = self._documents(arguments)
            definitions: dict[str, PipelineDefinition] = {}
            for graph in documents:
                if isinstance(graph, PipelineConfiguration):
                    definition = load_definition(graph.name, arguments.definition)
                    build_plan(graph, definition)
                    if definition is not None:
                        definitions[graph.name] = definition
            backend = open_backend(arguments.database)
            backend.register(
                [doc.model_dump(mode="json") for doc in documents],
                definitions=definitions,
            )
            print(json.dumps({"registered": len(documents)}))
        except Exception as error:
            print(f"provium dispatch: {error}", file=sys.stderr)
            return 2
        return 0

    @staticmethod
    def _assert_arguments_are_valid(arguments: argparse.Namespace) -> None:
        assert_database_is_configured(arguments)
        assert_graph_options_are_valid(arguments)
        if not arguments.file:
            raise ValueError("register requires YAML documents")

    @staticmethod
    def _documents(
        arguments: argparse.Namespace,
    ) -> list[PipelineConfiguration | ComputeProfile]:
        from provium_dispatch.documents import load_documents

        documents = load_documents(
            arguments.file, scratch_directory=arguments.scratch_directory
        )
        if not documents:
            raise ValueError("register requires YAML documents")
        return documents
