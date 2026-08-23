from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from provium_pipeline.sqlite_execution_store import (
    SCHEMA_VERSION,
    SQLiteExecutionStore,
    UnsupportedSchemaVersionError,
)

_EXPECTED_TABLES = {
    "schema_migrations",
    "runs",
    "tasks",
    "task_dependencies",
    "dispatches",
    "attempts",
    "computation_cache",
    "computation_reservations",
    "events",
}


def _tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def test_sqlite_store_bootstraps_forward_schema_and_local_pragmas(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"

    store = SQLiteExecutionStore(database, busy_timeout_ms=2_500)

    assert _EXPECTED_TABLES <= _tables(database)
    assert store.schema_version == SCHEMA_VERSION
    settings = store.database_settings()
    assert settings.foreign_keys
    assert settings.busy_timeout_ms == 2_500
    assert settings.journal_mode in {"wal", "memory", "delete"}


def test_sqlite_store_reopens_existing_schema_without_duplicate_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    SQLiteExecutionStore(database)

    reopened = SQLiteExecutionStore(database)

    assert reopened.schema_version == SCHEMA_VERSION
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()
    assert count == (SCHEMA_VERSION,)


def test_sqlite_store_rejects_database_from_newer_schema(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version) VALUES (?)",
            (SCHEMA_VERSION + 1,),
        )

    with pytest.raises(UnsupportedSchemaVersionError, match="newer"):
        SQLiteExecutionStore(database)


@pytest.mark.parametrize("busy_timeout_ms", [0, -1])
def test_sqlite_store_requires_positive_busy_timeout(
    tmp_path: Path,
    busy_timeout_ms: int,
) -> None:
    with pytest.raises(ValueError, match="positive"):
        SQLiteExecutionStore(
            tmp_path / "execution.sqlite3",
            busy_timeout_ms=busy_timeout_ms,
        )
