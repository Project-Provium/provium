"""Registration catalog for artifact definitions."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from provium.artifact.definition import Artifact, ArtifactDefinition


class ArtifactCatalog:
    """Map persistent artifact identifiers to their definitions."""

    def __init__(self) -> None:
        self._definitions: dict[str, ArtifactDefinition[Any]] = {}

    def register[ArtifactT: Artifact[Any, Any]](
        self,
        definition: ArtifactDefinition[ArtifactT],
    ) -> ArtifactDefinition[ArtifactT]:
        """Register and return an artifact definition."""
        if not isinstance(definition, ArtifactDefinition):
            raise TypeError("catalog entries must be ArtifactDefinition instances")
        if definition.identifier in self._definitions:
            raise ValueError(
                f"artifact identifier is already registered: {definition.identifier}"
            )
        self._definitions[definition.identifier] = definition
        return definition

    def resolve(self, identifier: str) -> ArtifactDefinition[Any]:
        """Return the definition registered for an identifier."""
        return self._definitions[identifier]

    @property
    def definitions(self) -> Mapping[str, ArtifactDefinition[Any]]:
        """Expose registered definitions through a read-only mapping."""
        return MappingProxyType(self._definitions)


__all__ = ["ArtifactCatalog"]
