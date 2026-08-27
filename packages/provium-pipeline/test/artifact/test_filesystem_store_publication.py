from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import provium_pipeline.artifact as artifact_module
import provium_pipeline.artifact.filesystem as filesystem_module
from provium_pipeline.artifact import (
    ArtifactLocationState,
    ArtifactStoreConflictError,
    ArtifactStoreCorruptionError,
    FilesystemArtifactStore,
    InvalidArtifactLocatorError,
    ManagedArtifactDescriptor,
)


def descriptor() -> ManagedArtifactDescriptor:
    return ManagedArtifactDescriptor(
        identity="abcdef-artifact-identity",
        artifact_identifier="example.document",
        body_digest="body-digest",
        container_digest="container-digest",
        size_bytes=14,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
    )


def install_inspection(
    monkeypatch: pytest.MonkeyPatch,
    managed: ManagedArtifactDescriptor,
) -> None:
    def inspect(path: Path) -> ManagedArtifactDescriptor:
        content = path.read_bytes()
        if content == b"artifact-bytes":
            return managed
        if content == b"different-bytes":
            return replace(managed, body_digest="different")
        raise ArtifactStoreCorruptionError("invalid finalized artifact")

    monkeypatch.setattr(artifact_module, "describe_finalized_artifact", inspect)


def test_publish_uses_deterministic_layout_and_returns_readable_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = descriptor()
    install_inspection(monkeypatch, managed)
    source = tmp_path / "source.pa"
    source.write_bytes(b"artifact-bytes")
    root = tmp_path / "store"
    store = FilesystemArtifactStore(identifier="local", root=root)

    location = store.publish(source=source, descriptor=managed)

    destination = root / "objects" / "ab" / f"{managed.identity}.pa"
    assert destination.read_bytes() == b"artifact-bytes"
    assert location.store_identifier == "local"
    assert location.locator == {
        "path": f"objects/ab/{managed.identity}.pa",
    }
    assert location.state is ArtifactLocationState.ACTIVE
    assert location.size_bytes == len(b"artifact-bytes")
    assert location.verified_at is not None
    assert list((root / "staging").iterdir()) == []


def test_republishing_exact_artifact_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = descriptor()
    install_inspection(monkeypatch, managed)
    source = tmp_path / "source.pa"
    source.write_bytes(b"artifact-bytes")
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")

    first = store.publish(source=source, descriptor=managed)
    assert isinstance(first.locator, dict)
    object_locator = first.locator.get("path")
    assert isinstance(object_locator, str)
    object_path = tmp_path / "store" / object_locator
    original_inode = object_path.stat().st_ino
    second = store.publish(source=source, descriptor=managed)

    assert second.locator == first.locator
    assert object_path.stat().st_ino == original_inode


def test_filesystem_store_passes_reusable_conformance_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from provium_pipeline.artifact import run_artifact_store_conformance

    managed = descriptor()
    install_inspection(monkeypatch, managed)
    source = tmp_path / "conformance-source.pa"
    source.write_bytes(b"artifact-bytes")
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")

    run_artifact_store_conformance(
        store=store,
        source=source,
        descriptor=managed,
        workspace=tmp_path / "workspace",
    )


def test_publish_never_overwrites_conflicting_existing_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = descriptor()
    install_inspection(monkeypatch, managed)
    source = tmp_path / "source.pa"
    source.write_bytes(b"artifact-bytes")
    destination = tmp_path / "store" / "objects" / "ab" / f"{managed.identity}.pa"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"different-bytes")
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")

    with pytest.raises(ArtifactStoreConflictError):
        store.publish(source=source, descriptor=managed)

    assert destination.read_bytes() == b"different-bytes"


def test_publish_rejects_artifact_that_changes_during_staging_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = descriptor()
    source = tmp_path / "source.pa"
    source.write_bytes(b"artifact-bytes")
    root = tmp_path / "store"
    store = FilesystemArtifactStore(identifier="local", root=root)

    def inspect(path: Path) -> ManagedArtifactDescriptor:
        if path == source:
            return managed
        return replace(managed, body_digest="changed-during-copy")

    monkeypatch.setattr(artifact_module, "describe_finalized_artifact", inspect)

    with pytest.raises(ArtifactStoreCorruptionError, match="staged"):
        store.publish(source=source, descriptor=managed)

    assert list((root / "staging").iterdir()) == []
    assert not (root / "objects" / "ab" / f"{managed.identity}.pa").exists()


def test_non_durable_publication_skips_durability_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = descriptor()
    install_inspection(monkeypatch, managed)
    source = tmp_path / "source.pa"
    source.write_bytes(b"artifact-bytes")
    store = FilesystemArtifactStore(
        identifier="local",
        root=tmp_path / "store",
        durable=False,
    )

    location = store.publish(source=source, descriptor=managed)

    assert location.size_bytes == managed.size_bytes


@pytest.mark.parametrize("identity", ["../../escape", ""])
def test_publish_rejects_identity_that_escapes_object_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    managed = replace(descriptor(), identity=identity)
    install_inspection(monkeypatch, managed)
    source = tmp_path / "source.pa"
    source.write_bytes(b"artifact-bytes")
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")

    with pytest.raises(InvalidArtifactLocatorError, match="identity"):
        store.publish(source=source, descriptor=managed)

    assert not (tmp_path / "escape.pa").exists()


def test_concurrent_conflicting_creation_is_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = descriptor()
    install_inspection(monkeypatch, managed)
    source = tmp_path / "source.pa"
    source.write_bytes(b"artifact-bytes")
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")
    real_link = filesystem_module.os.link

    def racing_link(source_path: Path, destination_path: Path) -> None:
        destination_path.write_bytes(b"different-bytes")
        real_link(source_path, destination_path)

    monkeypatch.setattr(filesystem_module.os, "link", racing_link)

    with pytest.raises(ArtifactStoreConflictError):
        store.publish(source=source, descriptor=managed)

    destination = tmp_path / "store" / "objects" / "ab" / f"{managed.identity}.pa"
    assert destination.read_bytes() == b"different-bytes"


def test_publish_rejects_source_that_does_not_match_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = descriptor()
    install_inspection(monkeypatch, managed)
    source = tmp_path / "source.pa"
    source.write_bytes(b"different-bytes")
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")

    with pytest.raises(ArtifactStoreCorruptionError, match="descriptor"):
        store.publish(source=source, descriptor=managed)
