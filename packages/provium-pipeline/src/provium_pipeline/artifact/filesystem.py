"""Filesystem-backed managed artifact storage."""

from __future__ import annotations

import fcntl
import os
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, BinaryIO, cast

if TYPE_CHECKING:
    from . import ArtifactLocation, ManagedArtifactDescriptor


def describe_finalized_artifact(path: Path) -> ManagedArtifactDescriptor:
    from . import describe_finalized_artifact as describe

    return describe(path)


class FilesystemArtifactStore:
    """Store finalized artifacts at deterministic paths beneath one root."""

    def __init__(self, *, identifier: str, root: Path, durable: bool = True) -> None:
        self._identifier = identifier
        self._root = root.resolve()
        self._durable = durable
        for directory in ("objects", "staging", "materialized", "locks"):
            (self._root / directory).mkdir(parents=True, exist_ok=True)

    @property
    def identifier(self) -> str:
        return self._identifier

    def publish(
        self,
        *,
        source: Path,
        descriptor: ManagedArtifactDescriptor,
    ) -> ArtifactLocation:
        from . import ArtifactStoreCorruptionError

        inspected = describe_finalized_artifact(source)
        if inspected != descriptor:
            raise ArtifactStoreCorruptionError(
                "source artifact does not match its managed descriptor"
            )

        destination = self._object_path(descriptor.identity)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._identity_lock(descriptor.identity):
            if destination.exists():
                self._verify_existing(destination, descriptor)
                return self._location(destination, descriptor)
            self._publish_new(source, destination, descriptor)
        return self._location(destination, descriptor)

    def _object_path(self, identity: str) -> Path:
        from . import InvalidArtifactLocatorError

        objects_root = self._root / "objects"
        candidate = objects_root / identity[:2] / f"{identity}.pa"
        if not identity or not candidate.resolve().is_relative_to(objects_root):
            raise InvalidArtifactLocatorError(
                f"artifact identity {identity!r} cannot form a safe object locator"
            )
        return candidate

    @contextmanager
    def _identity_lock(self, identity: str) -> Generator[None]:
        lock_path = self._root / "locks" / f"{identity}.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _publish_new(
        self,
        source: Path,
        destination: Path,
        descriptor: ManagedArtifactDescriptor,
    ) -> None:
        temporary_path: Path | None = None
        try:
            with source.open("rb") as source_stream:
                with NamedTemporaryFile(
                    mode="w+b",
                    dir=self._root / "staging",
                    prefix=f"{descriptor.identity}-",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    self._copy(source_stream, cast(BinaryIO, temporary))
            if describe_finalized_artifact(temporary_path) != descriptor:
                from . import ArtifactStoreCorruptionError

                raise ArtifactStoreCorruptionError(
                    "staged artifact does not match its managed descriptor"
                )
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                self._verify_existing(destination, descriptor)
            else:
                temporary_path.unlink()
                temporary_path = None
                if self._durable:
                    self._fsync_directory(destination.parent)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _copy(self, source: BinaryIO, destination: BinaryIO) -> None:
        shutil.copyfileobj(source, destination)
        destination.flush()
        if self._durable:
            os.fsync(destination.fileno())

    def _verify_existing(
        self,
        destination: Path,
        descriptor: ManagedArtifactDescriptor,
    ) -> None:
        from . import ArtifactStoreConflictError

        existing = describe_finalized_artifact(destination)
        if existing != descriptor:
            raise ArtifactStoreConflictError(
                f"artifact object {descriptor.identity!r} already contains "
                "different immutable metadata"
            )

    def _location(
        self,
        destination: Path,
        descriptor: ManagedArtifactDescriptor,
    ) -> ArtifactLocation:
        from . import ArtifactLocation, ArtifactLocationState

        verified_at = datetime.now(UTC)
        created_at = datetime.fromtimestamp(destination.stat().st_mtime, tz=UTC)
        relative_path = destination.relative_to(self._root).as_posix()
        return ArtifactLocation(
            store_identifier=self.identifier,
            locator={"path": relative_path},
            state=ArtifactLocationState.ACTIVE,
            size_bytes=descriptor.size_bytes,
            created_at=created_at,
            verified_at=verified_at,
        )

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = ["FilesystemArtifactStore"]
