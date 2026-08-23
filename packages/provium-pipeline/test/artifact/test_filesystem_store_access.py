from __future__ import annotations

import errno
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import provium_pipeline.artifact as artifact_module
import provium_pipeline.artifact.filesystem as filesystem_module
from provium_pipeline.artifact import (
    ArtifactLocation,
    ArtifactLocationState,
    ArtifactStoreCorruptionError,
    ArtifactStoreNotFoundError,
    ArtifactStorePermissionError,
    ArtifactStoreTransientError,
    FilesystemArtifactStore,
    InvalidArtifactLocatorError,
    ManagedArtifactDescriptor,
    MaterializationCleanup,
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
        if path.read_bytes() != b"artifact-bytes":
            raise ArtifactStoreCorruptionError("invalid finalized artifact")
        return managed

    monkeypatch.setattr(artifact_module, "describe_finalized_artifact", inspect)


def published_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    FilesystemArtifactStore,
    ManagedArtifactDescriptor,
    ArtifactLocation,
]:
    managed = descriptor()
    install_inspection(monkeypatch, managed)
    source = tmp_path / "source.pa"
    source.write_bytes(b"artifact-bytes")
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")
    location = store.publish(source=source, descriptor=managed)
    return store, managed, location


def location_path(root: Path, location: ArtifactLocation) -> Path:
    assert isinstance(location.locator, dict)
    relative_path = location.locator.get("path")
    assert isinstance(relative_path, str)
    return root / relative_path


def test_stat_validates_locator_and_reports_object_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)

    metadata = store.stat(location)

    assert metadata.size_bytes == managed.size_bytes
    assert metadata.modified_at is not None


def test_materialize_creates_verified_caller_owned_hard_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)
    destination = tmp_path / "work" / "input.pa"

    materialized = store.materialize(
        descriptor=managed,
        locations=(location,),
        destination=destination,
    )

    assert materialized.descriptor == managed
    assert materialized.path == destination
    assert materialized.cleanup is MaterializationCleanup.REQUIRED
    assert destination.read_bytes() == b"artifact-bytes"
    object_path = location_path(tmp_path / "store", location)
    assert destination.stat().st_ino == object_path.stat().st_ino


def test_materialize_copies_atomically_across_filesystems(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)
    destination = tmp_path / "work" / "input.pa"
    object_path = location_path(tmp_path / "store", location)
    real_link = filesystem_module.os.link

    def cross_filesystem_link(source: Path, target: Path) -> None:
        if source == object_path:
            raise OSError(errno.EXDEV, "cross-device link")
        real_link(source, target)

    monkeypatch.setattr(filesystem_module.os, "link", cross_filesystem_link)

    materialized = store.materialize(
        descriptor=managed,
        locations=(location,),
        destination=destination,
    )

    assert materialized.path == destination
    assert destination.read_bytes() == b"artifact-bytes"
    assert destination.stat().st_ino != object_path.stat().st_ino


def test_materialize_copy_handles_same_artifact_created_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)
    destination = tmp_path / "work" / "input.pa"
    object_path = location_path(tmp_path / "store", location)
    real_link = filesystem_module.os.link

    def racing_cross_filesystem_link(source: Path, target: Path) -> None:
        if source == object_path:
            raise OSError(errno.EXDEV, "cross-device link")
        target.write_bytes(b"artifact-bytes")
        real_link(source, target)

    monkeypatch.setattr(filesystem_module.os, "link", racing_cross_filesystem_link)

    materialized = store.materialize(
        descriptor=managed,
        locations=(location,),
        destination=destination,
    )

    assert materialized.path == destination
    assert destination.read_bytes() == b"artifact-bytes"


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (PermissionError(errno.EACCES, "denied"), ArtifactStorePermissionError),
        (OSError(errno.EIO, "I/O failure"), ArtifactStoreTransientError),
    ],
)
def test_materialize_maps_operational_link_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
    expected_error: type[Exception],
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)

    def fail_link(_: Path, __: Path) -> None:
        raise failure

    monkeypatch.setattr(filesystem_module.os, "link", fail_link)

    with pytest.raises(expected_error):
        store.materialize(
            descriptor=managed,
            locations=(location,),
            destination=tmp_path / "work" / "input.pa",
        )


def test_materialize_store_object_path_is_protected_and_not_caller_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)
    object_path = location_path(tmp_path / "store", location)

    materialized = store.materialize(
        descriptor=managed,
        locations=(location,),
        destination=object_path,
    )

    assert materialized.path == object_path
    assert materialized.cleanup is MaterializationCleanup.NOT_REQUIRED


def test_materialize_rejects_missing_managed_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)
    object_path = location_path(tmp_path / "store", location)
    object_path.unlink()

    with pytest.raises(ArtifactStoreNotFoundError):
        store.materialize(
            descriptor=managed,
            locations=(location,),
            destination=tmp_path / "input.pa",
        )


def test_stat_rejects_missing_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, location = published_store(tmp_path, monkeypatch)
    location_path(tmp_path / "store", location).unlink()

    with pytest.raises(ArtifactStoreNotFoundError):
        store.stat(location)


def test_materialize_requires_an_active_location_for_this_store(
    tmp_path: Path,
) -> None:
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")

    with pytest.raises(ArtifactStoreNotFoundError, match="no active"):
        store.materialize(
            descriptor=descriptor(),
            locations=(),
            destination=tmp_path / "input.pa",
        )


def test_materialize_rejects_object_with_different_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)

    def inspect(_: Path) -> ManagedArtifactDescriptor:
        return replace(managed, body_digest="different")

    monkeypatch.setattr(artifact_module, "describe_finalized_artifact", inspect)

    with pytest.raises(ArtifactStoreCorruptionError, match="descriptor"):
        store.materialize(
            descriptor=managed,
            locations=(location,),
            destination=tmp_path / "input.pa",
        )


def test_materialize_never_overwrites_corrupt_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)
    destination = tmp_path / "work" / "input.pa"
    destination.parent.mkdir()
    destination.write_bytes(b"corrupt")

    with pytest.raises(ArtifactStoreCorruptionError):
        store.materialize(
            descriptor=managed,
            locations=(location,),
            destination=destination,
        )

    assert destination.read_bytes() == b"corrupt"


def test_materialize_handles_same_artifact_created_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)
    destination = tmp_path / "work" / "input.pa"
    destination.parent.mkdir()
    real_link = filesystem_module.os.link

    def racing_link(source: Path, target: Path) -> None:
        target.write_bytes(b"artifact-bytes")
        real_link(source, target)

    monkeypatch.setattr(filesystem_module.os, "link", racing_link)

    materialized = store.materialize(
        descriptor=managed,
        locations=(location,),
        destination=destination,
    )

    assert materialized.path == destination
    assert destination.read_bytes() == b"artifact-bytes"


def test_stat_rejects_location_owned_by_another_store(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")
    location = ArtifactLocation(
        store_identifier="other",
        locator={"path": "objects/ab/artifact.pa"},
        state=ArtifactLocationState.ACTIVE,
        size_bytes=None,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        verified_at=None,
    )

    with pytest.raises(InvalidArtifactLocatorError):
        store.stat(location)


@pytest.mark.parametrize(
    "locator",
    [
        None,
        {"path": 42},
        {"path": "../../escape.pa"},
    ],
)
def test_stat_rejects_invalid_or_escaping_locator(
    tmp_path: Path,
    locator: object,
) -> None:
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")
    location = ArtifactLocation(
        store_identifier="local",
        locator=locator,  # type: ignore[arg-type]
        state=ArtifactLocationState.ACTIVE,
        size_bytes=None,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        verified_at=None,
    )

    with pytest.raises(InvalidArtifactLocatorError):
        store.stat(location)
