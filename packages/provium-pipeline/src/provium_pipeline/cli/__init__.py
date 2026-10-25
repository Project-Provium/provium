"""Lightweight pipeline command registration for the core Provium CLI."""

from provium.cli.catalog import CommandCatalog
from provium.cli.plugin import CLI_PLUGIN_API_VERSION, CLIPlugin

from provium_pipeline.cli.commands.pipeline import PipelineCommand

catalog = CommandCatalog()
catalog.register(PipelineCommand)
plugin = CLIPlugin(CLI_PLUGIN_API_VERSION, "provium-pipeline", catalog)

__all__ = ["PipelineCommand", "catalog", "plugin"]
