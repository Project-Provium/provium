"""Lightweight dispatch command registration for the core Provium CLI."""

from provium.cli.catalog import CommandCatalog
from provium.cli.plugin import CLI_PLUGIN_API_VERSION, CLIPlugin

from provium_dispatch.cli.commands.dispatch import DispatchCommand

catalog = CommandCatalog()
catalog.register(DispatchCommand)
plugin = CLIPlugin(CLI_PLUGIN_API_VERSION, "provium-dispatch", catalog)

__all__ = ["DispatchCommand", "catalog", "plugin"]
