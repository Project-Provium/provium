"""Filesystem-backed managed artifact storage."""

from __future__ import annotations

import errno
import fcntl
import os
import shutil
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, BinaryIO, cast

if TYPE_CHECKING:
    from . import (
        ArtifactLocation,
        ArtifactObjectMetadata,
        ManagedArtifactDescriptor,
        MaterializedArtifact,
        OrphanQuery,
    )


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

    def stat(self, location: ArtifactLocation) -> ArtifactObjectMetadata:
        from . import ArtifactObjectMetadata, ArtifactStoreNotFoundError

        path = self._path_from_location(location)
        try:
            metadata = path.stat()
        except FileNotFoundError as error:
            raise ArtifactStoreNotFoundError(
                f"artifact object does not exist at {path}"
            ) from error
        return ArtifactObjectMetadata(
            size_bytes=metadata.st_size,
            modified_at=datetime.fromtimestamp(metadata.st_mtime, tz=UTC),
        )

    def materialize(
        self,
        *,
        descriptor: ManagedArtifactDescriptor,
        locations: Sequence[ArtifactLocation],
        destination: Path,
    ) -> MaterializedArtifact:
        from . import (
            ArtifactStoreNotFoundError,
            MaterializationCleanup,
            MaterializedArtifact,
        )

        location = next(
            (
                candidate
                for candidate in locations
                if candidate.store_identifier == self.identifier
                and candidate.state.value == "active"
            ),
            None,
        )
        if location is None:
            raise ArtifactStoreNotFoundError(
                f"no active {self.identifier!r} location exists for "
                f"artifact {descriptor.identity!r}"
            )
        source = self._path_from_location(location)
        if not source.exists():
            raise ArtifactStoreNotFoundError(
                f"artifact object {descriptor.identity!r} does not exist"
            )
        self._verify_managed(source, descriptor)

        resolved_destination = destination.resolve()
        if resolved_destination == source:
            return MaterializedArtifact(
                descriptor=descriptor,
                path=source,
                cleanup=MaterializationCleanup.NOT_REQUIRED,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._verify_managed(destination, descriptor)
        else:
            try:
                os.link(source, destination)
            except FileExistsError:
                self._verify_managed(destination, descriptor)
            except PermissionError as error:
                from . import ArtifactStorePermissionError

                raise ArtifactStorePermissionError(str(error)) from error
            except OSError as error:
                if error.errno == errno.EXDEV:
                    self._copy_materialized(source, destination, descriptor)
                else:
                    from . import ArtifactStoreTransientError

                    raise ArtifactStoreTransientError(str(error)) from error
        self._verify_managed(destination, descriptor)
        return MaterializedArtifact(
            descriptor=descriptor,
            path=destination,
            cleanup=MaterializationCleanup.REQUIRED,
        )

    def delete(self, location: ArtifactLocation) -> None:
        from . import ArtifactStorePermissionError, ArtifactStoreTransientError

        path = self._path_from_location(location)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except PermissionError as error:
            raise ArtifactStorePermissionError(str(error)) from error
        except OSError as error:
            raise ArtifactStoreTransientError(str(error)) from error
        if self._durable:
            self._fsync_directory(path.parent)

    def list_orphans(self, request: OrphanQuery) -> tuple[ArtifactLocation, ...]:
        from . import ArtifactLocation, ArtifactLocationState

        locations: list[ArtifactLocation] = []
        for path in sorted((self._root / "objects").rglob("*.pa")):
            if path.is_symlink() or not path.is_file():
                continue
            metadata = path.stat()
            created_at = datetime.fromtimestamp(metadata.st_mtime, tz=UTC)
            if (
                request.created_before is not None
                and created_at >= request.created_before
            ):
                continue
            locations.append(
                ArtifactLocation(
                    store_identifier=self.identifier,
                    locator={"path": path.relative_to(self._root).as_posix()},
                    state=ArtifactLocationState.ACTIVE,
                    size_bytes=metadata.st_size,
                    created_at=created_at,
                    verified_at=None,
                )
            )
        return tuple(locations)

    def _copy_materialized(
        self,
        source: Path,
        destination: Path,
        descriptor: ManagedArtifactDescriptor,
    ) -> None:
        with NamedTemporaryFile(
            mode="w+b",
            dir=destination.parent,
            prefix=f".{destination.name}-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            try:
                with source.open("rb") as source_stream:
                    self._copy(source_stream, cast(BinaryIO, temporary))
                self._verify_managed(temporary_path, descriptor)
                try:
                    os.link(temporary_path, destination)
                except FileExistsError:
                    self._verify_managed(destination, descriptor)
            finally:
                temporary_path.unlink(missing_ok=True)

    def _path_from_location(self, location: ArtifactLocation) -> Path:
        from . import InvalidArtifactLocatorError

        locator = location.locator
        if (
            location.store_identifier != self.identifier
            or not isinstance(locator, dict)
        ):
            raise InvalidArtifactLocatorError(
                f"location is not valid for filesystem store {self.identifier!r}"
            )
        relative_path = locator.get("path")
        if not isinstance(relative_path, str):
            raise InvalidArtifactLocatorError(
                "filesystem artifact locator requires a string 'path'"
            )
        objects_root = self._root / "objects"
        candidate = (self._root / relative_path).resolve()
        if not candidate.is_relative_to(objects_root):
            raise InvalidArtifactLocatorError(
                "filesystem artifact locator escapes the object root"
            )
        return candidate

    def _verify_managed(
        self,
        path: Path,
        descriptor: ManagedArtifactDescriptor,
    ) -> None:
        from . import ArtifactStoreCorruptionError

        if describe_finalized_artifact(path) != descriptor:
            raise ArtifactStoreCorruptionError(
                f"artifact object {descriptor.identity!r} does not match its descriptor"
            )

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
