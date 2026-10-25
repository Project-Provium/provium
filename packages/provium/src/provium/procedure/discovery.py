"""Discover procedure catalogs published through package entry points."""

from importlib import metadata

from provium.procedure.catalog import ProcedureCatalog

ENTRY_POINT_GROUP = "provium.procedure_catalogs"
_discovered_catalog: ProcedureCatalog | None = None


def discover_procedure_catalogs() -> ProcedureCatalog:
    """Load and merge installed procedure catalogs."""
    global _discovered_catalog
    if _discovered_catalog is not None:
        return _discovered_catalog

    discovered = ProcedureCatalog()
    for entry_point in metadata.entry_points(group=ENTRY_POINT_GROUP):
        catalog = entry_point.load()
        if not isinstance(catalog, ProcedureCatalog):
            raise TypeError(
                f"procedure catalog entry point {entry_point.name!r} must expose "
                "a ProcedureCatalog"
            )
        for definition in catalog.definitions.values():
            discovered.register(definition)

    _discovered_catalog = discovered
    return discovered


def reset_procedure_discovery() -> None:
    """Clear cached procedure discovery state."""
    global _discovered_catalog
    _discovered_catalog = None


__all__ = [
    "ENTRY_POINT_GROUP",
    "discover_procedure_catalogs",
    "reset_procedure_discovery",
]
