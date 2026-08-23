"""Lightweight entry-point catalogs for the installed example plugin."""

from provium import (
    CLI_PLUGIN_API_VERSION,
    ArtifactCatalog,
    CLIPlugin,
    ProcedureCatalog,
)
from provium.cli import CommandCatalog

from .contracts import (
    FAILING_PROCEDURE,
    SOURCE_PROCEDURE,
    TEXT_ARTIFACT,
    TRANSFORM_PROCEDURE,
)

command_catalog = CommandCatalog()
cli_plugin = CLIPlugin(
    api_version=CLI_PLUGIN_API_VERSION,
    implementation="provium_example_plugin.catalogs:command_catalog",
    catalog=command_catalog,
)

artifact_catalog = ArtifactCatalog()
artifact_catalog.register(TEXT_ARTIFACT)

procedure_catalog = ProcedureCatalog()
procedure_catalog.register(SOURCE_PROCEDURE)
procedure_catalog.register(TRANSFORM_PROCEDURE)
procedure_catalog.register(FAILING_PROCEDURE)
