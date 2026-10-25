"""Nested ownership scopes for artifact resources."""

from __future__ import annotations

import hashlib
import io
import struct
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, BinaryIO, Protocol, Self, cast

from provium.artifact.header import (
    CONTAINER_VERSION,
    MAGIC,
    PREFIX_SIZE,
    ArtifactHeader,
    decode_header,
)
from provium.artifact.reader import ArtifactReader
from provium.artifact.region import BodyRegion
from provium.context import activate_context, current_context
from provium.files import local_file
from provium.provenance import ArtifactLineage, ArtifactRecord, ArtifactReference

if TYPE_CHECKING:
    from provium.artifact.binding import ArtifactReadBinding


class _Closeable(Protocol):
    def close(self) -> None: ...


class Session:
    """Own resources within a single-use, nestable logical context."""

    def __init__(self, *, scratch_directory: str | Path | None = None) -> None:
        self.scratch_directory = scratch_directory
        if scratch_directory is not None:
            Path(scratch_directory).mkdir(parents=True, exist_ok=True)
        self.active = False
        self.parent: Session | None = None
        self._used = False
        self._activation: AbstractContextManager[None] | None = None
        self._managed_resources: list[_Closeable] = []
        self._readers: list[ArtifactReader] = []
        self._inputs: dict[str, ArtifactRecord] = {}
        self._input_lineage = ArtifactLineage()

    def __enter__(self) -> Self:
        if self._used:
            raise RuntimeError("session has already been entered")
        parent = current_context()
        if parent is not None and not isinstance(parent, Session):
            raise RuntimeError("active artifact context is not a session")
        self._used = True
        self.parent = parent
        if self.scratch_directory is None and parent is not None:
            self.scratch_directory = parent.scratch_directory
        self.active = True
        activation = activate_context(self)
        activation.__enter__()
        self._activation = activation
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.active or current_context() is not self or self._activation is None:
            raise RuntimeError("session is not active")
        activation = self._activation
        close_error: Exception | None = None
        try:
            close_error = self._close_resources()
        finally:
            self.active = False
            self._activation = None
            activation.__exit__(exc_type, exc_value, traceback)
        if exc_type is None and close_error is not None:
            raise close_error

    def manage(self, resource: _Closeable) -> None:
        """Register a resource to close when this session exits."""
        if not self.active or current_context() is not self:
            raise RuntimeError("managed resources require the active session")
        if not callable(getattr(resource, "close", None)):
            raise TypeError("managed resource must provide a callable close operation")
        self._managed_resources.append(resource)

    @property
    def readers(self) -> tuple[ArtifactReader, ...]:
        """Return every reader opened directly by this session."""
        return tuple(self._readers)

    @property
    def inputs(self) -> tuple[ArtifactRecord, ...]:
        """Return unique semantic artifact inputs in opening order."""
        inherited = (
            {}
            if self.parent is None
            else {record.reference.identity: record for record in self.parent.inputs}
        )
        inherited.update(self._inputs)
        return tuple(inherited.values())

    @property
    def input_lineage(self) -> ArtifactLineage:
        """Return the merged lineage of every opened artifact."""
        if self.parent is None:
            return self._input_lineage
        return self.parent.input_lineage.merge(self._input_lineage)

    def open_artifact(self, binding: ArtifactReadBinding[Any]) -> ArtifactReader:
        """Open and verify one typed artifact binding."""
        from provium.procedure.authorization import expected_input_identity

        if not self.active or current_context() is not self:
            raise RuntimeError("artifact opening requires the active session")
        expected_identity = expected_input_identity(binding)
        downloads = ExitStack()
        self.manage(downloads)
        path = downloads.enter_context(
            local_file(binding.path, scratch_directory=self.scratch_directory)
        )
        stream = path.open("rb")
        try:
            header, file_length = self._read_header(stream)
            expected_identifier = binding.artifact.definition.identifier
            self._assert_opened_header_is_valid(
                header, file_length, expected_identifier
            )
            self._assert_expected_identity_matches(header, expected_identity)
            self._verify_digest(
                stream, header.body_offset, header.body_length, header.body_digest
            )
            reference = ArtifactReference(
                header.artifact_identity,
                header.artifact_identifier,
            )
            record = header.lineage.artifact(reference)
            merged_lineage = self._input_lineage.merge(header.lineage)
            region = BodyRegion(
                stream,
                header.body_offset,
                header.body_length,
                self,
                close_stream=True,
            )
            reader_type = cast(
                type[ArtifactReader],
                getattr(binding.artifact, "reader"),
            )
            reader = reader_type(region, header)
        except BaseException:
            stream.close()
            raise
        self.manage(reader)
        self._readers.append(reader)
        self._inputs.setdefault(record.reference.identity, record)
        self._input_lineage = merged_lineage
        return reader

    @staticmethod
    def _assert_expected_identity_matches(
        header: ArtifactHeader,
        expected_identity: str | None,
    ) -> None:
        if (
            expected_identity is not None
            and header.artifact_identity != expected_identity
        ):
            raise RuntimeError("artifact input identity changed after registration")

    @staticmethod
    def _read_header(stream: BinaryIO) -> tuple[ArtifactHeader, int]:
        prefix = stream.read(PREFIX_SIZE)
        if len(prefix) < PREFIX_SIZE:
            raise ValueError("truncated fixed header")
        magic, version, metadata_offset, metadata_length, _, _ = struct.unpack(
            ">8sHQQQQ", prefix
        )
        if magic != MAGIC:
            raise ValueError("invalid artifact magic bytes")
        if version != CONTAINER_VERSION:
            raise ValueError(f"unsupported container version: {version}")
        metadata_end = metadata_offset + metadata_length
        stream.seek(0)
        header = decode_header(stream.read(metadata_end))
        stream.seek(0, io.SEEK_END)
        return header, stream.tell()

    @staticmethod
    def _assert_opened_header_is_valid(
        header: ArtifactHeader,
        file_length: int,
        expected_identifier: str,
    ) -> None:
        if header.artifact_identifier != expected_identifier:
            raise TypeError("artifact does not match the requested artifact type")
        if header.body_offset + header.body_length > file_length:
            raise ValueError("artifact body is truncated")

    @staticmethod
    def _verify_digest(
        stream: BinaryIO,
        body_offset: int,
        body_length: int,
        expected_digest: str,
    ) -> None:
        digest = hashlib.sha256()
        stream.seek(body_offset)
        remaining = body_length
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("artifact body is truncated during checksum")
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != expected_digest:
            raise ValueError("artifact body digest does not match")

    def _owns_active_context(self) -> bool:
        """Return whether this session owns the current nested session."""
        current = current_context()
        while isinstance(current, Session):
            if current is self:
                return self.active
            current = current.parent
        return False

    def _close_resources(self) -> Exception | None:
        first_error: Exception | None = None
        for resource in reversed(self._managed_resources):
            try:
                resource.close()
            except Exception as error:  # noqa: BLE001
                if first_error is None:
                    first_error = error
        return first_error


class PersistentSession(Session):
    """Retain resources while activating only around procedure lifecycle hooks."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def __enter__(self) -> Self:
        if self.closed:
            raise RuntimeError("persistent session is closed")
        if self.active:
            raise RuntimeError("persistent session is already active")
        parent = current_context()
        if parent is not None and not isinstance(parent, Session):
            raise RuntimeError("active artifact context is not a session")
        self.parent = parent
        if self.scratch_directory is None and parent is not None:
            self.scratch_directory = parent.scratch_directory
        self.active = True
        activation = activate_context(self)
        activation.__enter__()
        self._activation = activation
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.active or current_context() is not self or self._activation is None:
            raise RuntimeError("persistent session is not active")
        activation = self._activation
        self.active = False
        self._activation = None
        activation.__exit__(exc_type, exc_value, traceback)

    def close(self) -> None:
        """Close retained resources exactly once in their owning context."""
        if self.closed:
            return
        close_error: Exception | None = None
        self.__enter__()
        try:
            close_error = self._close_resources()
        finally:
            try:
                self.__exit__(None, None, None)
            finally:
                self.closed = True
        if close_error is not None:
            raise close_error


def session(*, scratch_directory: str | Path | None = None) -> Session:
    """Create a new artifact resource session."""
    return Session(scratch_directory=scratch_directory)


def current_session() -> Session | None:
    """Return the active session, if any."""
    current = current_context()
    return current if isinstance(current, Session) else None


__all__ = ["Session", "current_session", "session"]
