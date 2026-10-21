"""Register pipeline and profile documents."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from provium.cli.command import Command

if TYPE_CHECKING:
    from ...models import ComputeProfile, PipelineConfiguration

from ..arguments import configure_graph, require_database, validate_graph_options


class RegisterCommand(Command):
    name = "register"
    help = "Register pipeline and profile documents"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_graph(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            self._validate(arguments)
            from ...definitions import load_definition
            from ...models import PipelineConfiguration, PipelineDefinition
            from ...persistence.factory import open_backend
            from ...planning import build_plan

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
            print(f"provium pipeline: {error}", file=sys.stderr)
            return 2
        return 0

    @staticmethod
    def _validate(arguments: argparse.Namespace) -> None:
        require_database(arguments)
        validate_graph_options(arguments)
        if not arguments.file:
            raise ValueError("register requires YAML documents")

    @staticmethod
    def _documents(
        arguments: argparse.Namespace,
    ) -> list[PipelineConfiguration | ComputeProfile]:
        from ...models import load_documents

        documents = load_documents(
            arguments.file, scratch_directory=arguments.scratch_directory
        )
        if not documents:
            raise ValueError("register requires YAML documents")
        return documents
