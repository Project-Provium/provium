"""Base class for typed artifact writers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Self

from provium.artifact.header import ArtifactHeader
from provium.artifact.region import BodyRegion
from provium.provenance import ArtifactLineage


class ArtifactWriter:
    """Own writable body access separately from container finalization."""

    def __init__(
        self,
        body: BodyRegion,
        metadata: ArtifactHeader,
        *,
        finalizer: Callable[[ArtifactWriter], None] | None = None,
    ) -> None:
        if not isinstance(body, BodyRegion):
            raise TypeError("body must be a BodyRegion")
        if not isinstance(metadata, ArtifactHeader):
            raise TypeError("header must be an ArtifactHeader")
        if finalizer is not None and not callable(finalizer):
            raise TypeError("finalizer must be callable or None")
        self._body = body
        self._metadata = metadata
        self._finalizer = finalizer
        self._container_finalized = False

    @property
    def metadata(self) -> ArtifactHeader:
        return self._metadata

    @property
    def identity(self) -> str:
        return self._metadata.artifact_identity

    @property
    def artifact_identifier(self) -> str:
        return self._metadata.artifact_identifier

    @property
    def lineage(self) -> ArtifactLineage:
        return self._metadata.lineage

    @property
    def body(self) -> BodyRegion:
        self._body.assert_access_is_allowed()
        return self._body

    @property
    def closed(self) -> bool:
        return self._body.closed

    @property
    def body_complete(self) -> bool:
        return self.closed

    @property
    def container_finalized(self) -> bool:
        return self._container_finalized

    def close(self) -> None:
        self._body.close()

    def finalize(self, metadata: ArtifactHeader | None = None) -> None:
        if self._container_finalized:
            return
        if not self.closed:
            self.close()
        else:
            self._body.assert_context_is_active()
        if self._finalizer is not None:
            self._finalizer(self)
        if metadata is not None:
            self._replace_metadata(metadata)
        self._assert_final_metadata_is_valid(self._metadata)
        self._container_finalized = True

    def __enter__(self) -> Self:
        self._body.assert_access_is_allowed()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _replace_metadata(self, metadata: ArtifactHeader) -> None:
        """Replace provisional metadata during owning-context finalization."""
        self._assert_final_metadata_is_valid(metadata)
        self._metadata = metadata

    def _assert_final_metadata_is_valid(self, metadata: ArtifactHeader) -> None:
        if not isinstance(metadata, ArtifactHeader):
            raise TypeError("metadata must be an ArtifactHeader")
        self._body.assert_context_is_active()
        if not self.body_complete:
            raise RuntimeError("artifact body must be complete before finalization")
        if metadata.body_length != self._body.length:
            raise ValueError("metadata body length does not match the completed body")
        if metadata.artifact_identity != self.identity:
            raise ValueError("metadata artifact identity changed during finalization")
        if metadata.artifact_identifier != self.artifact_identifier:
            raise ValueError("metadata artifact identifier changed during finalization")
        if metadata.body_offset != self._metadata.body_offset:
            raise ValueError("metadata body offset changed during finalization")


__all__ = ["ArtifactWriter"]
