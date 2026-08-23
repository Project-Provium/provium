"""Services that bridge transient finalized files into managed storage."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import ArtifactStore, ManagedArtifactDescriptor
    from .index import ArtifactIndex


def _describe_finalized_artifact(path: Path) -> ManagedArtifactDescriptor:
    from . import describe_finalized_artifact

    return describe_finalized_artifact(path)


class ArtifactImportService:
    """Import fully verified finalized artifacts into managed storage and indexing."""

    def __init__(self, *, store: ArtifactStore, index: ArtifactIndex) -> None:
        self._store = store
        self.index = index

    def import_artifact(self, source: Path) -> ManagedArtifactDescriptor:
        descriptor = _describe_finalized_artifact(source)
        self.index.register_artifact(descriptor)
        locations = self.index.get_active_locations(descriptor.identity)
        if not any(
            location.store_identifier == self._store.identifier
            for location in locations
        ):
            location = self._store.publish(source=source, descriptor=descriptor)
            self.index.register_location(descriptor.identity, location)
        return descriptor


__all__ = ["ArtifactImportService"]
