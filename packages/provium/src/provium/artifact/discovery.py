"""Discover artifact catalogs published through package entry points."""

from importlib import metadata

from provium.artifact.catalog import ArtifactCatalog

ENTRY_POINT_GROUP = "provium.artifact_catalogs"
_discovered_catalog: ArtifactCatalog | None = None


def discover_artifact_catalogs() -> ArtifactCatalog:
    """Load and merge installed artifact catalogs."""
    global _discovered_catalog
    if _discovered_catalog is not None:
        return _discovered_catalog

    discovered = ArtifactCatalog()
    for entry_point in metadata.entry_points().select(group=ENTRY_POINT_GROUP):
        catalog = entry_point.load()
        if not isinstance(catalog, ArtifactCatalog):
            raise TypeError(
                f"catalog entry point {entry_point.name!r} must expose "
                "an ArtifactCatalog"
            )
        for definition in catalog.definitions.values():
            discovered.register(definition)

    _discovered_catalog = discovered
    return discovered


def reset_artifact_discovery() -> None:
    """Clear cached artifact discovery state."""
    global _discovered_catalog
    _discovered_catalog = None


__all__ = ["discover_artifact_catalogs", "reset_artifact_discovery"]
