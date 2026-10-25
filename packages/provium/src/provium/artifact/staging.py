"""Disk-backed staging for artifact outputs."""

from __future__ import annotations

import hashlib
import io
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast
from uuid import uuid4

from provium.artifact.binding import ArtifactWriteBinding
from provium.artifact.header import ArtifactHeader, encode_header
from provium.artifact.region import BodyRegion
from provium.artifact.writer import ArtifactWriter
from provium.context import current_context
from provium.files import publish_file

if TYPE_CHECKING:
    from provium.session import Session


class StagedArtifact[WriterT: ArtifactWriter]:
    """Own one unpublished temporary container and its typed writer."""

    def __init__(
        self,
        destination: Path | str,
        temporary_path: Path,
        stream: BinaryIO,
        body: BodyRegion,
        writer: WriterT,
        owner: Session,
    ) -> None:
        self.destination = destination
        self.temporary_path = temporary_path
        self._stream = stream
        self._body = body
        self.writer = writer
        self._owner = owner
        self._aborted = False
        self._published = False
        self._body_summary: tuple[int, str] | None = None

    @property
    def aborted(self) -> bool:
        """Return whether this staged output has been abandoned."""
        return self._aborted

    @property
    def published(self) -> bool:
        """Return whether the staged container replaced its destination."""
        return self._published

    def finalize_body(self) -> tuple[int, str]:
        """Close the body and return its final length and SHA-256 digest."""
        self._assert_available()
        if self._body_summary is not None:
            return self._body_summary
        self.writer.close()
        self._body_summary = self._read_body_summary()
        return self._body_summary

    def _read_body_summary(self) -> tuple[int, str]:
        self._stream.flush()
        digest = hashlib.sha256()
        self._stream.seek(self.writer.metadata.body_offset)
        remaining = self._body.length
        while remaining:
            chunk = self._stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("staged artifact body is truncated")
            digest.update(chunk)
            remaining -= len(chunk)
        return self._body.length, digest.hexdigest()

    def publish(self, metadata: ArtifactHeader) -> None:
        """Finalize and atomically replace the destination container."""
        if self._published:
            return
        if not isinstance(metadata, ArtifactHeader):
            raise TypeError("metadata must be an ArtifactHeader")
        self.finalize_body()
        length, digest = self._read_body_summary()
        if metadata.body_length != length:
            raise ValueError("final metadata body length does not match staged body")
        if metadata.body_digest != digest:
            raise ValueError("final metadata body digest does not match staged body")
        encoded = encode_header(metadata)
        self.writer.finalize(metadata)
        self._stream.seek(0, io.SEEK_SET)
        self._stream.write(encoded)
        self._stream.flush()
        self._stream.close()
        publish_file(self.temporary_path, self.destination)
        self._published = True

    def abort(self) -> None:
        """Close and remove this unpublished output."""
        if self._aborted or self._published:
            return
        try:
            self.writer.close()
        finally:
            self._stream.close()
            self.temporary_path.unlink(missing_ok=True)
            self._aborted = True

    def close(self) -> None:
        """Abandon the output when its owning session closes."""
        self.abort()

    def _assert_available(self) -> None:
        if self._aborted:
            raise RuntimeError("staged artifact has been aborted")
        if not self._owner.active or current_context() is not self._owner:
            raise RuntimeError("staged artifact requires its active session")


def stage_artifact[WriterT: ArtifactWriter](
    binding: ArtifactWriteBinding[WriterT],
    metadata: ArtifactHeader,
    owner: Session,
) -> StagedArtifact[WriterT]:
    """Create an adjacent temporary container for one typed output."""
    _assert_staging_is_valid(binding, metadata, owner)
    destination = binding.path
    if isinstance(destination, Path):
        temporary_path = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    else:
        cleanup = ExitStack()
        owner.manage(cleanup)
        directory = cleanup.enter_context(
            tempfile.TemporaryDirectory(
                prefix="provium-output-", dir=owner.scratch_directory
            )
        )
        temporary_path = Path(directory) / "artifact.pa"
    stream = temporary_path.open("x+b")
    try:
        _reserve_body_offset(stream, metadata.body_offset)
        region = BodyRegion(
            stream,
            metadata.body_offset,
            0,
            owner,
            writable=True,
            close_stream=False,
        )
        writer_type = cast(
            type[WriterT],
            getattr(binding.artifact, "writer"),
        )
        writer = writer_type(region, metadata)
        staged = StagedArtifact(
            destination,
            temporary_path,
            stream,
            region,
            writer,
            owner,
        )
        owner.manage(staged)
    except BaseException:
        stream.close()
        temporary_path.unlink(missing_ok=True)
        raise
    return staged


def _assert_staging_is_valid(
    binding: ArtifactWriteBinding[Any],
    metadata: ArtifactHeader,
    owner: Session,
) -> None:
    if not owner.active or current_context() is not owner:
        raise RuntimeError("artifact staging requires the active session")
    if not isinstance(metadata, ArtifactHeader):
        raise TypeError("metadata must be an ArtifactHeader")
    if metadata.artifact_identifier != binding.artifact.definition.identifier:
        raise ValueError("metadata artifact identifier does not match the binding")
    if metadata.body_length != 0:
        raise ValueError("staged artifact metadata must have an empty body")


def _reserve_body_offset(stream: BinaryIO, body_offset: int) -> None:
    stream.seek(body_offset - 1)
    stream.write(b"\0")
    stream.flush()


__all__ = ["StagedArtifact", "stage_artifact"]
