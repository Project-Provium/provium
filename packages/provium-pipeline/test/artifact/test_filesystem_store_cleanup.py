from __future__ import annotations

import errno
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from provium_pipeline.artifact import (
    ArtifactStoreNotFoundError,
    ArtifactStorePermissionError,
    ArtifactStoreTransientError,
    FilesystemArtifactStore,
    OrphanQuery,
)
from test.artifact.test_filesystem_store_access import published_store


def test_delete_is_idempotent_and_object_becomes_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, location = published_store(tmp_path, monkeypatch)

    store.delete(location)
    store.delete(location)

    with pytest.raises(ArtifactStoreNotFoundError):
        store.stat(location)


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (PermissionError(errno.EACCES, "denied"), ArtifactStorePermissionError),
        (OSError(errno.EIO, "I/O failure"), ArtifactStoreTransientError),
    ],
)
def test_delete_maps_operational_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
    expected_error: type[Exception],
) -> None:
    store, _, location = published_store(tmp_path, monkeypatch)

    def fail_unlink(_: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise failure

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(expected_error):
        store.delete(location)


def test_non_durable_delete_skips_directory_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, location = published_store(tmp_path, monkeypatch)
    store = FilesystemArtifactStore(
        identifier="local",
        root=tmp_path / "store",
        durable=False,
    )

    store.delete(location)

    with pytest.raises(ArtifactStoreNotFoundError):
        store.stat(location)


def test_list_orphans_returns_deterministic_locations_before_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, managed, location = published_store(tmp_path, monkeypatch)

    candidates = store.list_orphans(
        OrphanQuery(created_before=datetime.now(UTC) + timedelta(seconds=1))
    )
    assert len(candidates) == 1
    assert candidates[0].locator == location.locator
    assert candidates[0].verified_at is None
    assert (
        store.list_orphans(OrphanQuery(created_before=datetime(2000, 1, 1, tzinfo=UTC)))
        == ()
    )
    assert store.list_orphans(OrphanQuery())[0].size_bytes == managed.size_bytes


def test_orphan_listing_ignores_symlinks_and_non_files(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    store = FilesystemArtifactStore(identifier="local", root=store_root)
    external = tmp_path / "external.pa"
    external.write_bytes(b"outside")
    object_directory = store_root / "objects" / "ab"
    object_directory.mkdir(parents=True)
    (object_directory / "linked.pa").symlink_to(external)
    (object_directory / "directory.pa").mkdir()

    assert store.list_orphans(OrphanQuery()) == ()


def test_empty_store_has_no_orphans(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(identifier="local", root=tmp_path / "store")

    assert store.list_orphans(OrphanQuery()) == ()
