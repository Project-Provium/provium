"""Stable contract for command-line plugins."""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import CommandCatalog

CLI_PLUGIN_API_VERSION = 1
"""The CLI plugin API version supported by this Provium release."""

CLI_PLUGIN_ENTRY_POINT_GROUP = "provium.cli.command_catalogs"
"""The packaging entry-point group used to publish CLI plugins."""


@dataclass(frozen=True, slots=True)
class CLIPlugin:
    """A versioned command catalog published by an installed distribution."""

    api_version: int
    implementation: str
    catalog: CommandCatalog

    def __post_init__(self) -> None:
        if not self.implementation.strip():
            raise ValueError("implementation must not be blank")
        if not isinstance(self.catalog, CommandCatalog):
            raise TypeError("catalog must be a CommandCatalog")


__all__ = [
    "CLI_PLUGIN_API_VERSION",
    "CLI_PLUGIN_ENTRY_POINT_GROUP",
    "CLIPlugin",
]
