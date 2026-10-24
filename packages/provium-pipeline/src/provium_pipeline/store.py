"""Verified local/S3 artifacts and atomic result-set publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import unquote, urlparse
from uuid import uuid4

from provium.artifact.inspection import inspect_finalized_artifact
from provium.provenance import ArtifactReference
from provium.semantic import artifact_fingerprint


@dataclass(frozen=True)
class Artifact:
    location: str
    path: str | None
    identity: str
    fingerprint: dict[str, str]


def local_path(location: str) -> Path:
    parsed = urlparse(location)
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost"):
            raise ValueError("file URL must reference the local host")
        return Path(unquote(parsed.path)).resolve()
    if parsed.scheme:
        raise ValueError(f"unsupported storage protocol: {parsed.scheme}")
    return Path(location).resolve()


@runtime_checkable
class ArtifactCache(Protocol):
    """Storage used by execution, lookup, and publication.

    Implementations may persist bytes in files, object storage, or a database.
    Materialization returns a verified local file for procedure readers.
    The writer guard must cover the entire direct execution, including publication.
    """

    namespace: str
    scratch: Path
    root: Path | None

    def writer_lock(self) -> AbstractContextManager[None]: ...
    def inspect(self, location: str) -> Artifact: ...
    def stage(
        self, computation: str, paths: dict[str, str], expected: dict[str, str]
    ) -> dict[str, dict[str, Any]]: ...
    def commit(self, computation: str, outputs: dict[str, Any]) -> None: ...
    def manifest(self, computation: str) -> dict[str, Any] | None: ...
    def restore(
        self, outputs: dict[str, Any], expected: dict[str, str]
    ) -> dict[str, Artifact]: ...
    def materialize(self, artifact: Artifact) -> Artifact: ...
    def verify(
        self, outputs: dict[str, Any], expected: dict[str, str]
    ) -> dict[str, Artifact]: ...


class ArtifactStore:
    def __init__(self, location: str, *, scratch: str | Path | None = None) -> None:
        self.scratch = Path(scratch or tempfile.gettempdir()).resolve()
        self.scratch.mkdir(parents=True, exist_ok=True)
        self._downloads = tempfile.TemporaryDirectory(
            prefix="provium-downloads-", dir=self.scratch
        )
        parsed = urlparse(location)
        self.remote = parsed.scheme == "s3"
        self.client: Any = None
        if self.remote:
            if not parsed.netloc or parsed.query or parsed.fragment:
                raise ValueError("invalid S3 location")
            import boto3

            sdk: Any = boto3
            self.client = sdk.client("s3")
            self.bucket = parsed.netloc
            self.prefix = parsed.path.strip("/")
            self.namespace = f"s3://{self.bucket}" + (
                f"/{self.prefix}" if self.prefix else ""
            )
            self.root = None
        else:
            self.root = local_path(location)
            self.root.mkdir(parents=True, exist_ok=True)
            self.namespace = self.root.as_uri()

    def close(self) -> None:
        """Remove this store instance's downloaded files, never committed artifacts."""
        self._downloads.cleanup()

    def __enter__(self) -> ArtifactStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @contextmanager
    def writer_lock(self) -> Generator[None]:
        """Reject overlapping direct writers on this host."""
        import fcntl

        digest = hashlib.sha256(self.namespace.encode()).hexdigest()
        path = (
            self.root / ".writer.lock"
            if self.root is not None
            else Path(tempfile.gettempdir()) / f"provium-writer-{digest}.lock"
        )
        with path.open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "another direct writer owns this store; use PostgreSQL"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _location(self, relative: str) -> str:
        return self.namespace + "/" + relative

    @staticmethod
    def _key(key: str) -> str:
        if not re.fullmatch("[0-9a-f]{64}", key):
            raise ValueError("invalid provenance key")
        return key

    def fetch(self, location: str) -> Path:
        parsed = urlparse(location)
        if parsed.scheme != "s3":
            return local_path(location)
        if not parsed.netloc or not parsed.path.strip("/"):
            raise ValueError("invalid S3 artifact location")
        client = self.client
        if client is None:
            import boto3

            sdk: Any = boto3
            client = sdk.client("s3")
        path = Path(self._downloads.name) / f"download-{uuid4().hex}.pa"
        try:
            client.download_file(parsed.netloc, parsed.path.lstrip("/"), str(path))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def inspect(self, location: str) -> Artifact:
        path = self.fetch(location)
        if not location.startswith("s3://"):
            location = path.as_uri()
        inspection = inspect_finalized_artifact(path)
        reference = ArtifactReference(
            inspection.artifact_identity, inspection.artifact_identifier
        )
        fingerprint = artifact_fingerprint(inspection.lineage, reference)
        if fingerprint["content"] != inspection.body_digest:
            raise ValueError("artifact lineage content disagrees with body")
        return Artifact(location, str(path), reference.identity, fingerprint)

    def _put(self, relative: str, source: Path) -> None:
        if self.remote:
            key = "/".join(part for part in (self.prefix, relative) if part)
            try:
                self.client.upload_file(str(source), self.bucket, key)
            except Exception:
                # An upload may have succeeded even when its acknowledgement was lost.
                recovered: Path | None = None
                try:
                    recovered = self.fetch(self._location(relative))
                    with source.open("rb") as first, recovered.open("rb") as second:
                        matches = (
                            hashlib.file_digest(first, "sha256").digest()
                            == hashlib.file_digest(second, "sha256").digest()
                        )
                    if matches:
                        return
                except Exception:
                    pass
                finally:
                    if recovered is not None:
                        recovered.unlink(missing_ok=True)
                raise
        else:
            assert self.root is not None
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{uuid4().hex}.tmp")
            try:
                with source.open("rb") as incoming, temporary.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                os.replace(temporary, destination)
                directory = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)

    def stage(
        self, computation: str, paths: dict[str, str], expected: dict[str, str]
    ) -> dict[str, dict[str, Any]]:
        self._key(computation)
        if set(paths) != set(expected):
            raise ValueError("incomplete output set")
        verified = {name: self.inspect(path) for name, path in paths.items()}
        for name, artifact in verified.items():
            if artifact.fingerprint["provenance"] != expected[name]:
                raise ValueError(f"wrong output provenance: {name}")
        # A unique immutable version isolates failed/stale publication attempts.
        generation = uuid4().hex
        outputs: dict[str, dict[str, Any]] = {}
        for name, artifact in verified.items():
            assert artifact.path is not None
            with Path(artifact.path).open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            relative = f"artifacts/{computation}/{generation}/{digest}.pa"
            self._put(relative, Path(artifact.path))
            outputs[name] = {
                "location": self._location(relative),
                "identity": artifact.identity,
                "fingerprint": artifact.fingerprint,
            }
        return outputs

    def commit(self, computation: str, outputs: dict[str, Any]) -> None:
        """Publish a direct single-writer result; queued runs commit in PostgreSQL."""
        self._key(computation)
        temporary = self.scratch / f"manifest-{uuid4().hex}.json"
        try:
            temporary.write_text(json.dumps(outputs, sort_keys=True))
            self._put(f"results/{computation}.json", temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def manifest(self, computation: str) -> dict[str, Any] | None:
        self._key(computation)
        relative = f"results/{computation}.json"
        if self.remote:
            from botocore.exceptions import ClientError

            key = "/".join(part for part in (self.prefix, relative) if part)
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=key)
                with response["Body"] as stream:
                    return json.loads(stream.read())
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                    return None
                raise
        assert self.root is not None
        try:
            return json.loads((self.root / relative).read_text())
        except FileNotFoundError:
            return None

    def restore(
        self, outputs: dict[str, Any], expected: dict[str, str]
    ) -> dict[str, Artifact]:
        """Load committed references without fetching or reading artifact files."""
        if set(outputs) != set(expected):
            raise ValueError("incomplete committed result")
        result: dict[str, Artifact] = {}
        for name, metadata in outputs.items():
            fingerprint = metadata["fingerprint"]
            if (
                set(fingerprint) != {"type", "content", "provenance"}
                or not all(
                    isinstance(value, str) and value for value in fingerprint.values()
                )
                or fingerprint["provenance"] != expected[name]
                or not isinstance(metadata["location"], str)
                or not metadata["location"]
                or not isinstance(metadata["identity"], str)
                or not metadata["identity"]
            ):
                raise ValueError(f"invalid committed artifact metadata: {name}")
            self._key(fingerprint["content"])
            self._key(fingerprint["provenance"])
            result[name] = Artifact(
                metadata["location"], None, metadata["identity"], dict(fingerprint)
            )
        return result

    def materialize(self, artifact: Artifact) -> Artifact:
        """Fetch and verify a deferred reference before a procedure consumes it."""
        if artifact.path is not None:
            return artifact
        verified = self.inspect(artifact.location)
        if (
            verified.identity != artifact.identity
            or verified.fingerprint != artifact.fingerprint
        ):
            raise ValueError("committed artifact integrity/provenance mismatch")
        return verified

    def verify(
        self, outputs: dict[str, Any], expected: dict[str, str]
    ) -> dict[str, Artifact]:
        if set(outputs) != set(expected):
            raise ValueError("incomplete committed result")
        result: dict[str, Artifact] = {}
        for name, metadata in outputs.items():
            artifact = self.inspect(metadata["location"])
            if (
                artifact.identity != metadata["identity"]
                or artifact.fingerprint != metadata["fingerprint"]
                or artifact.fingerprint["provenance"] != expected[name]
            ):
                raise ValueError(
                    f"committed artifact integrity/provenance mismatch: {name}"
                )
            result[name] = artifact
        return result

    def lookup(
        self, computation: str, expected: dict[str, str]
    ) -> dict[str, Artifact] | None:
        outputs = self.manifest(computation)
        return None if outputs is None else self.verify(outputs, expected)
