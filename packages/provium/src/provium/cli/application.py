"""Parser construction and command dispatch."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from provium import __version__

from .catalog import CommandCatalog
from .completion import enable_completion
from .configuration import configure_configuration
from .discovery import discover_command_catalogs
from .errors import EXPECTED_CLI_ERRORS, print_cli_error
from .group import COMMAND_DESTINATION, configure_catalog


def create_parser(catalog: CommandCatalog) -> argparse.ArgumentParser:
    """Create a parser containing the commands in a catalog."""
    parser = argparse.ArgumentParser(
        prog="provium",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"provium {__version__}",
    )
    configure_configuration(parser)
    configure_catalog(parser, catalog, destination="command_name")
    return parser


def run(
    arguments: Sequence[str],
    *,
    catalog: CommandCatalog | None = None,
) -> int:
    """Parse arguments and execute the selected command."""
    selected_catalog = discover_command_catalogs() if catalog is None else catalog
    parser = create_parser(selected_catalog)
    enable_completion(parser)
    parsed = parser.parse_args(arguments)
    command = getattr(parsed, COMMAND_DESTINATION)
    return command.execute(parsed)


def main() -> int:
    """Run the command-line interface with the process arguments."""
    try:
        return run(sys.argv[1:])
    except EXPECTED_CLI_ERRORS as error:
        return print_cli_error("running the Provium CLI", error)


__all__ = ["create_parser", "main", "run"]
