"""SQLite execution-store schema bootstrap and connection policy."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1


class UnsupportedSchemaVersionError(RuntimeError):
    """The database schema is newer than this package understands."""


def _require_supported_schema(version: int) -> None:
    if version > SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            "database schema is newer than this package"
        )


@dataclass(frozen=True, slots=True)
class SQLiteDatabaseSettings:
    """Effective safety and concurrency settings for one connection."""

    foreign_keys: bool
    journal_mode: str
    busy_timeout_ms: int


_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS runs ("
    "id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tasks ("
    "id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, "
    "state TEXT NOT NULL, payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS task_dependencies ("
    "task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, "
    "dependency_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, "
    "PRIMARY KEY(task_id, dependency_id))",
    "CREATE TABLE IF NOT EXISTS dispatches ("
    "id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, "
    "payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS attempts ("
    "id TEXT PRIMARY KEY, task_id TEXT NOT NULL "
    "REFERENCES tasks(id) ON DELETE CASCADE, "
    "lease_token TEXT, lease_expires_at TEXT, payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS computation_cache ("
    "computation_key TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS computation_reservations ("
    "computation_key TEXT PRIMARY KEY, owner_task_id TEXT NOT NULL, "
    "expires_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS events ("
    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, task_id TEXT, "
    "kind TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)",
)


class SQLiteExecutionStore:
    """Durable local execution store, introduced through forward migrations."""

    def __init__(
        self,
        database: Path,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self._database = database
        self._busy_timeout_ms = busy_timeout_ms
        database.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @property
    def schema_version(self) -> int:
        """Return the highest migration applied to the database."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
        return int(row[0])

    def database_settings(self) -> SQLiteDatabaseSettings:
        """Return effective per-connection SQLite safety settings."""
        with self._connection() as connection:
            foreign_keys = bool(connection.execute("PRAGMA foreign_keys").fetchone()[0])
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
            timeout = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
        return SQLiteDatabaseSettings(foreign_keys, journal_mode, timeout)

    def _migrate(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL "
                    "DEFAULT CURRENT_TIMESTAMP)"
                )
                row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()
                version = int(row[0])
                _require_supported_schema(version)
                if version < 1:
                    for statement in _SCHEMA_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (1)"
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
        finally:
            connection.close()
