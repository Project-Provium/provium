"""Local and S3 file locations with scoped, disk-backed downloads."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from provium.context import current_context

type FileLocation = str | os.PathLike[str]


def normalize_location(location: FileLocation) -> Path | str:
    """Preserve S3 URIs and normalize local paths and file:// URLs."""
    value = os.fspath(location)
    parsed = urlparse(value)
    if parsed.scheme == "s3":
        if (
            not parsed.netloc
            or not parsed.path.strip("/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("S3 file location requires a bucket and object key")
        return value
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            raise ValueError("invalid local file URL")
        return Path(unquote(parsed.path))
    if parsed.scheme:
        raise ValueError(f"unsupported file scheme: {parsed.scheme}")
    return Path(value)


def _s3_client() -> Any:
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("S3 paths require installing provium[s3]") from error
    sdk: Any = boto3
    return sdk.client("s3")


@contextmanager
def local_file(
    location: FileLocation, *, scratch_directory: FileLocation | None = None
) -> Generator[Path]:
    """Yield a local readable path; remove owned downloads on exit."""
    if scratch_directory is None:
        scratch_directory = getattr(current_context(), "scratch_directory", None)
    normalized = normalize_location(location)
    if isinstance(normalized, Path):
        yield normalized
        return
    parsed = urlparse(normalized)
    if scratch_directory is not None:
        Path(scratch_directory).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="provium-file-", dir=scratch_directory
    ) as directory:
        path = Path(directory) / ("file" + Path(parsed.path).suffix)
        client = _s3_client()
        from botocore.exceptions import ClientError

        try:
            client.download_file(parsed.netloc, parsed.path[1:], str(path))
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                raise FileNotFoundError(normalized) from error
            raise
        yield path


def publish_file(source: Path, destination: FileLocation) -> None:
    """Install a complete file locally or upload it to S3, then remove staging."""
    normalized = normalize_location(destination)
    if isinstance(normalized, Path):
        source.replace(normalized)
    else:
        parsed = urlparse(normalized)
        _s3_client().upload_file(str(source), parsed.netloc, parsed.path[1:])
        source.unlink()


def location_identity(location: FileLocation) -> Path | str:
    """Canonical local destination or unchanged S3 object URI."""
    normalized = normalize_location(location)
    return normalized.resolve() if isinstance(normalized, Path) else normalized


def remove_file(location: FileLocation) -> None:
    """Remove a local file or S3 object during publication rollback."""
    normalized = normalize_location(location)
    if isinstance(normalized, Path):
        normalized.unlink(missing_ok=True)
    else:
        parsed = urlparse(normalized)
        _s3_client().delete_object(Bucket=parsed.netloc, Key=parsed.path[1:])


@contextmanager
def writable_file(
    location: FileLocation, *, scratch_directory: FileLocation | None = None
) -> Generator[Path]:
    """Yield a local output path and upload only on successful completion."""
    normalized = normalize_location(location)
    if isinstance(normalized, Path):
        yield normalized
        return
    if scratch_directory is None:
        scratch_directory = getattr(current_context(), "scratch_directory", None)
    if scratch_directory is not None:
        Path(scratch_directory).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="provium-output-", dir=scratch_directory
    ) as directory:
        path = Path(directory) / ("file" + Path(urlparse(normalized).path).suffix)
        yield path
        publish_file(path, normalized)


def file_exists(location: FileLocation) -> bool:
    """Check existence without treating denied access as an absent object."""
    normalized = normalize_location(location)
    if isinstance(normalized, Path):
        return normalized.exists()
    client = _s3_client()
    from botocore.exceptions import ClientError

    parsed = urlparse(normalized)
    try:
        client.head_object(Bucket=parsed.netloc, Key=parsed.path[1:])
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise
    return True
