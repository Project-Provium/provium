# PYTHON_ARGCOMPLETE_OK
"""Provium's command-line interface and command plugin system."""

from importlib.metadata import version

from provium.cli.application import create_parser, main, run
from provium.cli.catalog import CommandCatalog
from provium.cli.command import Command
from provium.cli.discovery import discover_command_catalogs, reset_command_discovery
from provium.cli.group import CommandGroup
from provium.cli.plugin import (
    CLI_PLUGIN_API_VERSION,
    CLI_PLUGIN_ENTRY_POINT_GROUP,
    CLIPlugin,
)

__version__ = version("provium")

__all__ = [
    "CLI_PLUGIN_API_VERSION",
    "CLI_PLUGIN_ENTRY_POINT_GROUP",
    "CLIPlugin",
    "Command",
    "CommandCatalog",
    "CommandGroup",
    "__version__",
    "create_parser",
    "discover_command_catalogs",
    "main",
    "reset_command_discovery",
    "run",
]
