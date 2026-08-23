from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import provium_pipeline.artifact as artifact_module
from provium_pipeline.artifact import (
    ArtifactIdentityCollisionError,
    ArtifactImportService,
    ArtifactLocation,
    ArtifactLocationState,
    ArtifactStore,
    InMemoryArtifactIndex,
    ManagedArtifactDescriptor,
)


def descriptor() -> ManagedArtifactDescriptor:
    return ManagedArtifactDescriptor(
        identity="artifact-identity",
        artifact_identifier="example.document",
        body_digest="body-digest",
        container_digest="container-digest",
        size_bytes=42,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
    )


class PublishingStore:
    identifier = "managed"

    def __init__(self) -> None:
        self.published: list[tuple[Path, ManagedArtifactDescriptor]] = []

    def publish(
        self,
        *,
        source: Path,
        descriptor: ManagedArtifactDescriptor,
    ) -> ArtifactLocation:
        self.published.append((source, descriptor))
        return ArtifactLocation(
            store_identifier=self.identifier,
            locator={"identity": descriptor.identity},
            state=ArtifactLocationState.ACTIVE,
            size_bytes=descriptor.size_bytes,
            created_at=descriptor.created_at,
            verified_at=descriptor.created_at,
        )


def service(store: PublishingStore) -> ArtifactImportService:
    return ArtifactImportService(
        store=cast(ArtifactStore, store),
        index=InMemoryArtifactIndex(),
    )


def use_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    managed: ManagedArtifactDescriptor,
) -> None:
    def inspect(_: Path) -> ManagedArtifactDescriptor:
        return managed

    monkeypatch.setattr(artifact_module, "describe_finalized_artifact", inspect)


def test_import_verifies_publishes_and_registers_logical_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path("transient/source.pa")
    managed = descriptor()
    store = PublishingStore()
    importer = service(store)
    use_descriptor(monkeypatch, managed)

    imported = importer.import_artifact(source)

    assert imported == managed
    assert store.published == [(source, managed)]
    assert importer.index.get_artifact(managed.identity) == managed
    assert importer.index.get_active_locations(managed.identity) == (
        ArtifactLocation(
            store_identifier="managed",
            locator={"identity": managed.identity},
            state=ArtifactLocationState.ACTIVE,
            size_bytes=42,
            created_at=managed.created_at,
            verified_at=managed.created_at,
        ),
    )


def test_reimport_skips_publication_when_store_location_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = descriptor()
    store = PublishingStore()
    importer = service(store)
    use_descriptor(monkeypatch, managed)

    importer.import_artifact(Path("first.pa"))
    importer.import_artifact(Path("second.pa"))

    assert store.published == [(Path("first.pa"), managed)]


def test_identity_collision_stops_before_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = descriptor()
    store = PublishingStore()
    importer = service(store)
    importer.index.register_artifact(managed)
    use_descriptor(
        monkeypatch,
        replace(managed, body_digest="different"),
    )

    with pytest.raises(ArtifactIdentityCollisionError):
        importer.import_artifact(Path("collision.pa"))

    assert store.published == []
