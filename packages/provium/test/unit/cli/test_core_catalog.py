from __future__ import annotations

from provium.cli import CommandCatalog
from provium.cli.discovery import discover_command_catalogs


def test_core_catalog_registers_execute_artifact_and_graph_commands() -> None:
    from provium.cli.commands import catalog

    assert isinstance(catalog, CommandCatalog)
    assert tuple(catalog.commands) == ("execute", "artifact", "graph")


def test_discovery_always_includes_the_core_command_catalog() -> None:
    assert tuple(discover_command_catalogs().commands)[:3] == (
        "execute",
        "artifact",
        "graph",
    )
