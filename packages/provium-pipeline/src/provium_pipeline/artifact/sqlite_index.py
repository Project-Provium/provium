"""SQLite-backed durable artifact index."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from provium_pipeline.canonical import canonical_json

from .index import ArtifactIdentityCollisionError, ArtifactIndexNotFoundError

if TYPE_CHECKING:
    from . import ArtifactLocation, ManagedArtifactDescriptor

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS artifacts (
    identity TEXT PRIMARY KEY,
    artifact_identifier TEXT NOT NULL,
    body_digest TEXT NOT NULL,
    container_digest TEXT,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_locations (
    artifact_identity TEXT NOT NULL REFERENCES artifacts(identity) ON DELETE CASCADE,
    store_identifier TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    state TEXT NOT NULL,
    size_bytes INTEGER,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    PRIMARY KEY (artifact_identity, store_identifier, locator_json)
);
CREATE INDEX IF NOT EXISTS artifact_locations_active
    ON artifact_locations (artifact_identity, state, store_identifier, locator_json);
"""


class SQLiteArtifactIndex:
    """Durable transactional implementation of the core artifact index contract."""

    def __init__(self, database: Path) -> None:
        self._database = database.resolve()
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(_SCHEMA)

    def register_artifact(
        self, descriptor: ManagedArtifactDescriptor
    ) -> ManagedArtifactDescriptor:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM artifacts WHERE identity = ?",
                (descriptor.identity,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        identity, artifact_identifier, body_digest,
                        container_digest, size_bytes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        descriptor.identity,
                        descriptor.artifact_identifier,
                        descriptor.body_digest,
                        descriptor.container_digest,
                        descriptor.size_bytes,
                        descriptor.created_at.isoformat(),
                    ),
                )
            elif self._descriptor(row) != descriptor:
                raise ArtifactIdentityCollisionError(
                    f"artifact identity {descriptor.identity!r} is already registered "
                    "with different immutable metadata"
                )
        return descriptor

    def register_location(
        self,
        artifact_identity: str,
        location: ArtifactLocation,
    ) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_artifact(connection, artifact_identity)
            connection.execute(
                """
                INSERT INTO artifact_locations (
                    artifact_identity, store_identifier, locator_json, state,
                    size_bytes, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (artifact_identity, store_identifier, locator_json)
                DO UPDATE SET
                    state = excluded.state,
                    size_bytes = excluded.size_bytes,
                    created_at = excluded.created_at,
                    verified_at = excluded.verified_at
                """,
                (
                    artifact_identity,
                    location.store_identifier,
                    canonical_json(location.locator),
                    location.state.value,
                    location.size_bytes,
                    location.created_at.isoformat(),
                    self._optional_datetime(location.verified_at),
                ),
            )

    def deactivate_location(
        self,
        artifact_identity: str,
        location: ArtifactLocation,
    ) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_artifact(connection, artifact_identity)
            connection.execute(
                """
                UPDATE artifact_locations
                SET state = 'inactive'
                WHERE artifact_identity = ?
                  AND store_identifier = ?
                  AND locator_json = ?
                """,
                (
                    artifact_identity,
                    location.store_identifier,
                    canonical_json(location.locator),
                ),
            )

    def get_artifact(self, artifact_identity: str) -> ManagedArtifactDescriptor:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE identity = ?",
                (artifact_identity,),
            ).fetchone()
            if row is None:
                raise ArtifactIndexNotFoundError(
                    f"artifact identity {artifact_identity!r} is not registered"
                )
            return self._descriptor(row)

    def get_active_locations(
        self, artifact_identity: str
    ) -> tuple[ArtifactLocation, ...]:
        with self._connection() as connection:
            self._require_artifact(connection, artifact_identity)
            rows = connection.execute(
                """
                SELECT * FROM artifact_locations
                WHERE artifact_identity = ? AND state = 'active'
                ORDER BY store_identifier, locator_json
                """,
                (artifact_identity,),
            ).fetchall()
            return tuple(self._location(row) for row in rows)

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _require_artifact(
        connection: sqlite3.Connection,
        artifact_identity: str,
    ) -> None:
        exists = connection.execute(
            "SELECT 1 FROM artifacts WHERE identity = ?",
            (artifact_identity,),
        ).fetchone()
        if exists is None:
            raise ArtifactIndexNotFoundError(
                f"artifact identity {artifact_identity!r} is not registered"
            )

    @staticmethod
    def _descriptor(row: sqlite3.Row) -> ManagedArtifactDescriptor:
        from . import ManagedArtifactDescriptor

        return ManagedArtifactDescriptor(
            identity=cast(str, row["identity"]),
            artifact_identifier=cast(str, row["artifact_identifier"]),
            body_digest=cast(str, row["body_digest"]),
            container_digest=cast(str | None, row["container_digest"]),
            size_bytes=cast(int, row["size_bytes"]),
            created_at=datetime.fromisoformat(cast(str, row["created_at"])),
        )

    @staticmethod
    def _location(row: sqlite3.Row) -> ArtifactLocation:
        from . import ArtifactLocation, ArtifactLocationState, JsonValue

        locator = cast(JsonValue, json.loads(cast(str, row["locator_json"])))
        verified_value = cast(str | None, row["verified_at"])
        return ArtifactLocation(
            store_identifier=cast(str, row["store_identifier"]),
            locator=locator,
            state=ArtifactLocationState(cast(str, row["state"])),
            size_bytes=cast(int | None, row["size_bytes"]),
            created_at=datetime.fromisoformat(cast(str, row["created_at"])),
            verified_at=(
                datetime.fromisoformat(verified_value)
                if verified_value is not None
                else None
            ),
        )

    @staticmethod
    def _optional_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None


__all__ = ["SQLiteArtifactIndex"]
