"""Deterministic collections of core definition catalogs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from provium import (
    ArtifactCatalog,
    ArtifactDefinition,
    ProcedureCatalog,
    ProcedureDefinition,
)


class CatalogResolutionError(LookupError):
    """Raised when a compiler catalog lookup is missing or ambiguous."""


@dataclass(frozen=True, slots=True, init=False)
class ArtifactCatalogCollection:
    """Immutable ordered artifact-catalog search path."""

    catalogs: tuple[ArtifactCatalog, ...]

    def __init__(self, catalogs: Iterable[ArtifactCatalog]) -> None:
        object.__setattr__(self, "catalogs", tuple(catalogs))

    def resolve(self, identifier: str) -> ArtifactDefinition[Any]:
        """Resolve exactly one artifact definition."""
        matches: list[ArtifactDefinition[Any]] = []
        for catalog in self.catalogs:
            try:
                matches.append(catalog.resolve(identifier))
            except KeyError:
                continue
        if not matches:
            raise CatalogResolutionError(
                f"artifact definition is not registered: {identifier!r}"
            )
        if len(matches) > 1:
            raise CatalogResolutionError(
                f"ambiguous artifact definition {identifier!r} across catalogs"
            )
        return matches[0]


@dataclass(frozen=True, slots=True, init=False)
class ProcedureCatalogCollection:
    """Immutable ordered procedure-catalog search path."""

    catalogs: tuple[ProcedureCatalog, ...]

    def __init__(self, catalogs: Iterable[ProcedureCatalog]) -> None:
        object.__setattr__(self, "catalogs", tuple(catalogs))

    def resolve(self, identifier: str) -> ProcedureDefinition[Any]:
        """Resolve exactly one procedure definition."""
        matches: list[ProcedureDefinition[Any]] = []
        for catalog in self.catalogs:
            try:
                matches.append(catalog.resolve(identifier))
            except KeyError:
                continue
        if not matches:
            raise CatalogResolutionError(
                f"procedure definition is not registered: {identifier!r}"
            )
        if len(matches) > 1:
            raise CatalogResolutionError(
                f"ambiguous procedure definition {identifier!r} across catalogs"
            )
        return matches[0]
