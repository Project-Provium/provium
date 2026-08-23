from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import provium_pipeline.artifact.plugins as plugins_module
from provium_pipeline.artifact import (
    ARTIFACT_INDEX_ENTRY_POINT_GROUP,
    ARTIFACT_STORE_ENTRY_POINT_GROUP,
    ArtifactIndexFactory,
    ArtifactStoreFactory,
    FilesystemArtifactStore,
    SQLiteArtifactIndex,
    discover_artifact_index_factories,
    discover_artifact_store_factories,
    filesystem_artifact_store_factory,
    sqlite_artifact_index_factory,
)


@dataclass
class EntryPoint:
    name: str
    value: str
    provider: object
    loads: int = 0

    def load(self) -> object:
        self.loads += 1
        return self.provider


def store_factory(*, identifier: str, configuration: dict[str, Any]) -> object:
    return identifier, configuration


def index_factory(*, configuration: dict[str, Any]) -> object:
    return configuration


def test_artifact_plugin_entry_point_groups_are_stable() -> None:
    assert ARTIFACT_STORE_ENTRY_POINT_GROUP == "provium.artifact_stores"
    assert ARTIFACT_INDEX_ENTRY_POINT_GROUP == "provium.artifact_indexes"


def test_store_factory_discovery_is_deterministic_and_lazy_until_called() -> None:
    second = EntryPoint("second", "package:second", store_factory)
    first = EntryPoint("first", "package:first", store_factory)

    result = discover_artifact_store_factories(entry_points=(second, first))

    assert result.factories.names() == ("first", "second")
    assert result.diagnostics == ()
    assert first.loads == 1
    assert second.loads == 1
    assert result.factories.get("first") is store_factory
    assert result.factories.get("second") is store_factory
    with pytest.raises(KeyError):
        result.factories.get("missing")


def test_plugin_discovery_reports_invalid_and_duplicate_factories() -> None:
    result = discover_artifact_index_factories(
        entry_points=(
            EntryPoint("duplicate", "a:first", index_factory),
            EntryPoint("duplicate", "b:second", index_factory),
            EntryPoint("invalid", "c:invalid", object()),
        )
    )

    assert result.factories.names() == ("duplicate",)
    assert tuple(diagnostic.entry_point for diagnostic in result.diagnostics) == (
        "duplicate",
        "invalid",
    )
    assert all(diagnostic.error for diagnostic in result.diagnostics)


def test_builtin_artifact_factories_create_configured_adapters(tmp_path: Path) -> None:
    store = filesystem_artifact_store_factory(
        identifier="local",
        configuration={"root": str(tmp_path / "objects"), "durable": False},
    )
    index = sqlite_artifact_index_factory(
        configuration={"database": str(tmp_path / "index.sqlite3")},
    )

    assert isinstance(store, FilesystemArtifactStore)
    assert isinstance(index, SQLiteArtifactIndex)
    assert store.identifier == "local"


def test_discovery_uses_installed_entry_point_group_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_groups: list[str] = []
    entry_point = EntryPoint("installed", "package:factory", store_factory)

    class InstalledEntryPoints:
        def select(self, *, group: str) -> tuple[EntryPoint, ...]:
            selected_groups.append(group)
            return (entry_point,)

    monkeypatch.setattr(
        plugins_module.metadata,
        "entry_points",
        lambda: InstalledEntryPoints(),
    )

    result = discover_artifact_store_factories()

    assert selected_groups == [ARTIFACT_STORE_ENTRY_POINT_GROUP]
    assert result.factories.names() == ("installed",)


@pytest.mark.parametrize(
    ("factory", "configuration"),
    [
        (filesystem_artifact_store_factory, {}),
        (filesystem_artifact_store_factory, {"root": "objects", "durable": 1}),
        (sqlite_artifact_index_factory, {}),
    ],
)
def test_builtin_factories_reject_invalid_configuration(
    factory: object,
    configuration: dict[str, Any],
) -> None:
    with pytest.raises(TypeError):
        if factory is filesystem_artifact_store_factory:
            filesystem_artifact_store_factory(
                identifier="local",
                configuration=configuration,
            )
        else:
            sqlite_artifact_index_factory(configuration=configuration)


def test_builtin_factories_satisfy_public_protocols() -> None:
    store: ArtifactStoreFactory = filesystem_artifact_store_factory
    index: ArtifactIndexFactory = sqlite_artifact_index_factory

    assert callable(store)
    assert callable(index)
