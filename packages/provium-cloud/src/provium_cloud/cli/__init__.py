"""Register cloud commands with the core Provium CLI."""

from provium.cli.catalog import CommandCatalog
from provium.cli.plugin import CLI_PLUGIN_API_VERSION, CLIPlugin

from provium_cloud.cli.commands.cloud import CloudCommand

catalog = CommandCatalog()
catalog.register(CloudCommand)
plugin = CLIPlugin(CLI_PLUGIN_API_VERSION, "provium-cloud", catalog)

__all__ = ["CloudCommand", "catalog", "plugin"]
