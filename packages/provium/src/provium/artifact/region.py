"""Context-owned, body-relative binary stream regions."""

from __future__ import annotations

import io
import os
from typing import BinaryIO

from provium.context import current_context


class BodyRegion:
    """Expose one bounded artifact body using body-relative positions."""

    def __init__(
        self,
        stream: BinaryIO,
        body_offset: int,
        body_length: int,
        owner: object,
        *,
        writable: bool = False,
        close_stream: bool = False,
    ) -> None:
        if body_offset < 0 or body_length < 0:
            raise ValueError("body boundaries must be non-negative")
        self._stream = stream
        self._body_offset = body_offset
        self._length = body_length
        self._owner = owner
        self._writable = writable
        self._close_stream = close_stream
        self._position = 0
        self._closed = False

    @property
    def length(self) -> int:
        """Return the current logical body length."""
        return self._length

    @property
    def closed(self) -> bool:
        """Return whether this region has been closed."""
        return self._closed

    def assert_context_is_active(self) -> None:
        """Require this region's live owner to be active."""
        owns_active_context = getattr(self._owner, "_owns_active_context", None)
        owns_context = (
            owns_active_context()
            if callable(owns_active_context)
            else current_context() is self._owner
        )
        if not owns_context:
            raise RuntimeError("body region is not owned by the active context")
        if not getattr(self._owner, "active", False):
            raise RuntimeError("body region owner is no longer active")

    def assert_access_is_allowed(self) -> None:
        """Require active ownership and an open region."""
        self.assert_context_is_active()
        if self._closed:
            raise ValueError("body region is closed")

    def close(self) -> None:
        """Close this region and its stream when owned by the region."""
        if self._closed:
            return
        self.assert_context_is_active()
        self._closed = True
        if self._close_stream:
            self._stream.close()

    def tell(self) -> int:
        """Return the body-relative stream position."""
        self.assert_access_is_allowed()
        return self._position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        """Move to a body-relative position."""
        self.assert_access_is_allowed()
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self._position + offset
        elif whence == os.SEEK_END:
            position = self._length + offset
        else:
            raise ValueError("invalid seek whence")
        if position < 0:
            raise ValueError("cannot seek before the artifact body")
        if not self._writable and position > self._length:
            raise ValueError("cannot seek beyond the finalized artifact body")
        self._position = position
        self._position_stream()
        return position

    def read(self, size: int | None = -1) -> bytes:
        """Read without crossing the finalized body boundary."""
        self.assert_access_is_allowed()
        if self._writable:
            raise io.UnsupportedOperation("read")
        remaining = self._length - self._position
        read_size = remaining if size is None or size < 0 else min(size, remaining)
        self._position_stream()
        result = self._stream.read(read_size)
        self._position += len(result)
        return result

    def readinto(self, buffer: bytearray | memoryview) -> int:
        """Read body bytes into a writable buffer."""
        self.assert_access_is_allowed()
        if self._writable:
            raise io.UnsupportedOperation("read")
        target = memoryview(buffer).cast("B")
        result = self.read(len(target))
        target[: len(result)] = result
        return len(result)

    def write(self, data: bytes | bytearray | memoryview) -> int:
        """Write body bytes and extend the logical body when necessary."""
        self.assert_access_is_allowed()
        if not self._writable:
            raise io.UnsupportedOperation("write")
        self._position_stream()
        written = self._stream.write(data)
        self._position += written
        self._length = max(self._length, self._position)
        return written

    def flush(self) -> None:
        """Flush the underlying stream."""
        self.assert_access_is_allowed()
        self._stream.flush()

    def _position_stream(self) -> None:
        self._stream.seek(self._body_offset + self._position, os.SEEK_SET)


__all__ = ["BodyRegion"]
