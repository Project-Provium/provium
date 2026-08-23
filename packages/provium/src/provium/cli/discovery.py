"""Discover command catalogs published by installed distributions."""

from __future__ import annotations

from importlib import metadata
from typing import Any

from .catalog import CommandCatalog
from .plugin import CLI_PLUGIN_API_VERSION, CLI_PLUGIN_ENTRY_POINT_GROUP, CLIPlugin

ENTRY_POINT_GROUP = CLI_PLUGIN_ENTRY_POINT_GROUP

_discovered_catalog: CommandCatalog | None = None


def _distribution_name(entry_point: Any) -> str:
    distribution = getattr(entry_point, "dist", None)
    name = getattr(distribution, "name", None)
    return name if isinstance(name, str) and name else "unknown"


def _entry_point_catalog(entry_point: Any) -> CommandCatalog:
    loaded = entry_point.load()
    diagnostic = (
        f"CLI plugin entry point {entry_point.name!r} from distribution "
        f"{_distribution_name(entry_point)!r}"
    )
    if isinstance(loaded, CLIPlugin):
        if loaded.api_version != CLI_PLUGIN_API_VERSION:
            raise RuntimeError(
                f"{diagnostic} uses unsupported API version {loaded.api_version}; "
                f"this Provium installation supports version {CLI_PLUGIN_API_VERSION}"
            )
        return loaded.catalog
    if isinstance(loaded, CommandCatalog):
        return loaded
    raise TypeError(f"{diagnostic} must expose a CLIPlugin or legacy CommandCatalog")


def discover_command_catalogs() -> CommandCatalog:
    """Return the combined built-in and installed command catalog."""

    global _discovered_catalog
    if _discovered_catalog is not None:
        return _discovered_catalog

    from .commands import catalog as core_catalog

    discovered = CommandCatalog()
    for command in core_catalog.commands.values():
        discovered.register(command)

    for entry_point in metadata.entry_points(group=CLI_PLUGIN_ENTRY_POINT_GROUP):
        catalog = _entry_point_catalog(entry_point)
        for command in catalog.commands.values():
            discovered.register(command)

    _discovered_catalog = discovered
    return discovered


def reset_command_discovery() -> None:
    """Clear the cached discovered catalog."""

    global _discovered_catalog
    _discovered_catalog = None


__all__ = ["discover_command_catalogs", "reset_command_discovery"]
