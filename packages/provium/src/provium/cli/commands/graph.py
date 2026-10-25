"""Render provenance graphs or emit their source code."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from provium import session
from provium.artifact import read_artifact_header
from provium.cli.command import Command
from provium.cli.errors import EXPECTED_CLI_ERRORS, print_cli_error
from provium.files import file_exists, normalize_location, writable_file
from provium.tool.visualization import (
    lineage_to_dot,
    lineage_to_mermaid,
    render_lineage,
)


def _add_force_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-y",
        action="store_true",
        dest="force",
        help="overwrite an existing output without confirmation",
    )


def _add_label_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-a",
        "--show-artifact-identities",
        action="store_true",
        help="include artifact identity hashes in labels",
    )
    parser.add_argument(
        "-p",
        "--show-procedure-versions",
        action="store_true",
        help="include procedure version hashes in labels",
    )
    parser.add_argument(
        "-e",
        "--show-execution-identities",
        action="store_true",
        help="include execution identity hashes in labels",
    )


def _confirm_output(path: Path | str, *, force: bool) -> None:
    if not file_exists(path) or force:
        return
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"output already exists: {path}; use -y to overwrite non-interactively"
        )
    print(f"Overwrite {path}? [y/N] ", end="", file=sys.stderr, flush=True)
    response = sys.stdin.readline().strip().casefold()
    if response not in {"y", "yes"}:
        raise RuntimeError("output was not overwritten")


class GraphCommand(Command):
    """Render provenance graphs or emit graph source code."""

    name = "graph"
    help = "Visualize artifact provenance"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="graph_action", required=True)

        render_parser = actions.add_parser(
            "render", help="Render an artifact provenance graph"
        )
        render_parser.add_argument("artifact", type=normalize_location)
        render_parser.add_argument("output", type=normalize_location)
        render_parser.add_argument(
            "--backend",
            choices=("auto", "graphviz", "mermaid"),
            default="auto",
        )
        render_parser.add_argument("--scratch-directory")
        _add_label_arguments(render_parser)
        _add_force_argument(render_parser)
        render_parser.set_defaults(graph_handler=self._render)

        source_parser = actions.add_parser(
            "source", help="Write DOT or Mermaid graph source"
        )
        source_parser.add_argument("artifact", type=normalize_location)
        source_parser.add_argument("language", choices=("dot", "mermaid"))
        source_parser.add_argument("output", nargs="?", type=normalize_location)
        source_parser.add_argument("--scratch-directory")
        _add_label_arguments(source_parser)
        _add_force_argument(source_parser)
        source_parser.set_defaults(graph_handler=self._source)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            with session(scratch_directory=arguments.scratch_directory):
                arguments.graph_handler(arguments)
        except EXPECTED_CLI_ERRORS as error:
            return print_cli_error(f"generating {arguments.graph_action} graph", error)
        return 0

    @staticmethod
    def _render(arguments: argparse.Namespace) -> None:
        output: Path | str = arguments.output
        if not Path(str(output)).suffix:
            output = normalize_location(f"{output}.png")
        _confirm_output(output, force=arguments.force)
        lineage = read_artifact_header(arguments.artifact).lineage
        image = render_lineage(
            lineage,
            format=Path(str(output)).suffix[1:].lower(),
            backend=arguments.backend,
            show_artifact_identities=arguments.show_artifact_identities,
            show_procedure_versions=arguments.show_procedure_versions,
            show_execution_identities=arguments.show_execution_identities,
        )
        with writable_file(output) as local:
            local.write_bytes(image)

    @staticmethod
    def _source(arguments: argparse.Namespace) -> None:
        output: Path | str | None = arguments.output
        if output is not None:
            _confirm_output(output, force=arguments.force)
        lineage = read_artifact_header(arguments.artifact).lineage
        label_options = {
            name: enabled
            for name, enabled in {
                "show_artifact_identities": arguments.show_artifact_identities,
                "show_procedure_versions": arguments.show_procedure_versions,
                "show_execution_identities": arguments.show_execution_identities,
            }.items()
            if enabled
        }
        source = (
            lineage_to_dot(lineage, **label_options)
            if arguments.language == "dot"
            else lineage_to_mermaid(lineage, **label_options)
        )
        if output is None:
            sys.stdout.write(source)
        else:
            with writable_file(output) as local:
                local.write_text(source)


__all__ = ["GraphCommand"]
