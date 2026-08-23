"""Artifact store and index plugin factory contracts and discovery."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

if TYPE_CHECKING:
    from . import ArtifactStore, JsonValue
    from .index import ArtifactIndex

ARTIFACT_STORE_ENTRY_POINT_GROUP = "provium.artifact_stores"
ARTIFACT_INDEX_ENTRY_POINT_GROUP = "provium.artifact_indexes"


class ArtifactStoreFactory(Protocol):
    """Create one configured artifact store instance."""

    def __call__(
        self,
        *,
        identifier: str,
        configuration: Mapping[str, JsonValue],
    ) -> ArtifactStore: ...


class ArtifactIndexFactory(Protocol):
    """Create one configured artifact index instance."""

    def __call__(
        self,
        *,
        configuration: Mapping[str, JsonValue],
    ) -> ArtifactIndex: ...


class _EntryPoint(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def value(self) -> str: ...

    def load(self) -> Any: ...


FactoryT = TypeVar("FactoryT")


class ArtifactFactoryCatalog[FactoryT]:
    """Immutable deterministic collection of named plugin factories."""

    def __init__(self, registrations: Iterable[tuple[str, FactoryT]]) -> None:
        self._registrations = tuple(sorted(registrations, key=lambda item: item[0]))

    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._registrations)

    def get(self, name: str) -> FactoryT:
        for registered_name, factory in self._registrations:
            if registered_name == name:
                return factory
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class ArtifactPluginDiagnostic:
    """One artifact plugin that could not be registered."""

    entry_point: str
    value: str
    error: str


@dataclass(frozen=True, slots=True)
class ArtifactPluginDiscoveryResult[FactoryT]:
    """Successfully loaded factories plus isolated discovery diagnostics."""

    factories: ArtifactFactoryCatalog[FactoryT]
    diagnostics: tuple[ArtifactPluginDiagnostic, ...]


def discover_artifact_store_factories(
    *,
    entry_points: Iterable[_EntryPoint] | None = None,
) -> ArtifactPluginDiscoveryResult[ArtifactStoreFactory]:
    candidates = _entry_points(ARTIFACT_STORE_ENTRY_POINT_GROUP, entry_points)
    return cast(
        ArtifactPluginDiscoveryResult[ArtifactStoreFactory],
        _discover(candidates),
    )


def discover_artifact_index_factories(
    *,
    entry_points: Iterable[_EntryPoint] | None = None,
) -> ArtifactPluginDiscoveryResult[ArtifactIndexFactory]:
    candidates = _entry_points(ARTIFACT_INDEX_ENTRY_POINT_GROUP, entry_points)
    return cast(
        ArtifactPluginDiscoveryResult[ArtifactIndexFactory],
        _discover(candidates),
    )


def filesystem_artifact_store_factory(
    *,
    identifier: str,
    configuration: Mapping[str, JsonValue],
) -> ArtifactStore:
    """Create the built-in filesystem store from canonical JSON configuration."""
    from .filesystem import FilesystemArtifactStore

    root = configuration.get("root")
    durable = configuration.get("durable", True)
    if not isinstance(root, str):
        raise TypeError(
            "filesystem artifact store requires string configuration 'root'"
        )
    if not isinstance(durable, bool):
        raise TypeError("filesystem artifact store 'durable' must be a boolean")
    return FilesystemArtifactStore(
        identifier=identifier,
        root=Path(root),
        durable=durable,
    )


def sqlite_artifact_index_factory(
    *,
    configuration: Mapping[str, JsonValue],
) -> ArtifactIndex:
    """Create the built-in SQLite index from canonical JSON configuration."""
    from .sqlite_index import SQLiteArtifactIndex

    database = configuration.get("database")
    if not isinstance(database, str):
        raise TypeError(
            "SQLite artifact index requires string configuration 'database'"
        )
    return SQLiteArtifactIndex(Path(database))


def _entry_points(
    group: str,
    provided: Iterable[_EntryPoint] | None,
) -> Iterable[_EntryPoint]:
    if provided is not None:
        return provided
    discovered = metadata.entry_points().select(group=group)
    return cast(Iterable[_EntryPoint], discovered)


def _load_factory(
    entry_point: _EntryPoint,
    registrations: Mapping[str, object],
) -> object:
    provider = entry_point.load()
    if not callable(provider):
        raise TypeError("artifact plugin entry point must load a callable factory")
    if entry_point.name in registrations:
        raise ValueError(f"duplicate artifact plugin factory {entry_point.name!r}")
    return provider


def _discover(
    candidates: Iterable[_EntryPoint],
) -> ArtifactPluginDiscoveryResult[object]:
    registrations: dict[str, object] = {}
    diagnostics: list[ArtifactPluginDiagnostic] = []
    for entry_point in sorted(candidates, key=lambda item: (item.name, item.value)):
        try:
            registrations[entry_point.name] = _load_factory(
                entry_point,
                registrations,
            )
        except Exception as error:
            diagnostics.append(
                ArtifactPluginDiagnostic(
                    entry_point=entry_point.name,
                    value=entry_point.value,
                    error=f"{type(error).__name__}: {error}",
                )
            )
    return ArtifactPluginDiscoveryResult(
        factories=ArtifactFactoryCatalog(registrations.items()),
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "ARTIFACT_INDEX_ENTRY_POINT_GROUP",
    "ARTIFACT_STORE_ENTRY_POINT_GROUP",
    "ArtifactFactoryCatalog",
    "ArtifactIndexFactory",
    "ArtifactPluginDiagnostic",
    "ArtifactPluginDiscoveryResult",
    "ArtifactStoreFactory",
    "discover_artifact_index_factories",
    "discover_artifact_store_factories",
    "filesystem_artifact_store_factory",
    "sqlite_artifact_index_factory",
]
