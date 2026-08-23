from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from provium_pipeline.artifact import (
    ArtifactIdentityCollisionError,
    ArtifactIndexNotFoundError,
    ManagedArtifactDescriptor,
    SQLiteArtifactIndex,
)
from test.artifact.test_index import descriptor, location


def test_sqlite_index_persists_descriptors_and_locations_across_instances(
    tmp_path: Path,
) -> None:
    database = tmp_path / "artifact-index.sqlite3"
    writer = SQLiteArtifactIndex(database)
    managed = descriptor()
    stored_location = location()

    writer.register_artifact(managed)
    writer.register_location(managed.identity, stored_location)

    reader = SQLiteArtifactIndex(database)
    assert reader.get_artifact(managed.identity) == managed
    assert reader.get_active_locations(managed.identity) == (stored_location,)

    reader.deactivate_location(managed.identity, stored_location)
    assert writer.get_active_locations(managed.identity) == ()

    writer.register_location(managed.identity, stored_location)
    assert reader.get_active_locations(managed.identity) == (stored_location,)


def test_sqlite_index_enforces_immutable_identity_collision(
    tmp_path: Path,
) -> None:
    index = SQLiteArtifactIndex(tmp_path / "index.sqlite3")
    managed = descriptor()
    index.register_artifact(managed)

    assert index.register_artifact(managed) == managed
    with pytest.raises(ArtifactIdentityCollisionError):
        index.register_artifact(replace(managed, size_bytes=43))


def test_sqlite_index_rejects_unknown_artifacts(tmp_path: Path) -> None:
    index = SQLiteArtifactIndex(tmp_path / "index.sqlite3")

    with pytest.raises(ArtifactIndexNotFoundError):
        index.get_artifact("unknown")
    with pytest.raises(ArtifactIndexNotFoundError):
        index.register_location("unknown", location())
    with pytest.raises(ArtifactIndexNotFoundError):
        index.get_active_locations("unknown")


def test_sqlite_identity_registration_is_transactional_across_connections(
    tmp_path: Path,
) -> None:
    database = tmp_path / "index.sqlite3"
    first = SQLiteArtifactIndex(database)
    second = SQLiteArtifactIndex(database)
    managed = descriptor()

    def register(index: SQLiteArtifactIndex) -> ManagedArtifactDescriptor:
        return index.register_artifact(managed)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(register, (first, second)))

    assert results == (managed, managed)
    assert SQLiteArtifactIndex(database).get_artifact(managed.identity) == managed
