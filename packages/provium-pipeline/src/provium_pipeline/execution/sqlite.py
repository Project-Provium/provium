"""SQLite execution-store schema bootstrap and connection policy."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from provium_pipeline.execution.cancellation import cancelled_task_state
from provium_pipeline.execution.codec import (
    pipeline_run_from_json,
    pipeline_run_json,
    pipeline_task_from_json,
    pipeline_task_json,
)
from provium_pipeline.execution.store import RunIdempotencyConflictError
from provium_pipeline.execution.task_transitions import (
    transition_task as apply_task_transition,
)
from provium_pipeline.identifiers import RunId, TaskId
from provium_pipeline.run.models import (
    CreateRunRequest,
    PipelineRun,
    PipelineTask,
    RunPlan,
    RunState,
    TaskState,
    plan_run,
)
from provium_pipeline.run.transitions import apply_run_transition

SCHEMA_VERSION = 1


class UnsupportedSchemaVersionError(RuntimeError):
    """The database schema is newer than this package understands."""


def _require_supported_schema(version: int) -> None:
    if version > SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            "database schema is newer than this package"
        )


def _migration_checksum() -> str:
    payload = "\0".join(_SCHEMA_STATEMENTS).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_migration_checksum(version: int, checksum: str | None) -> str:
    expected = _migration_checksum()
    if checksum is not None and checksum != expected:
        raise MigrationChecksumError(
            f"migration checksum mismatch for version {version}"
        )
    return expected


class _RunPlanner(Protocol):
    def __call__(
        self,
        request: CreateRunRequest,
        *,
        now: datetime,
    ) -> RunPlan: ...


class MigrationChecksumError(RuntimeError):
    """An applied migration no longer matches its recorded definition."""


@dataclass(frozen=True, slots=True)
class SQLiteDatabaseSettings:
    """Effective safety and concurrency settings for one connection."""

    foreign_keys: bool
    journal_mode: str
    busy_timeout_ms: int


def _require_task_payload(row: tuple[object, ...] | None, identifier: TaskId) -> str:
    if row is None:
        raise KeyError(identifier)
    return str(row[0])


_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
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


def _require_run_payload(
    row: tuple[object, ...] | None,
    identifier: RunId,
) -> str:
    if row is None:
        raise KeyError(f"unknown run identifier: {identifier}")
    return str(row[0])


class SQLiteExecutionStore:
    """Durable local execution store, introduced through forward migrations."""

    def __init__(
        self,
        database: Path,
        *,
        busy_timeout_ms: int = 5_000,
        clock: Callable[[], datetime] | None = None,
        planner: _RunPlanner = plan_run,
    ) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self._database = database
        self._busy_timeout_ms = busy_timeout_ms
        self._clock = clock or (lambda: datetime.now(UTC))
        self._planner = planner
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

    def create_run(self, request: CreateRunRequest) -> PipelineRun:
        """Plan and atomically persist a run with its complete task graph."""
        scoped_key = self._scoped_key(request)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if scoped_key is not None:
                    existing = self._idempotent_run(connection, request, scoped_key)
                    if existing is not None:
                        connection.commit()
                        return existing
                plan = self._planner(request, now=self._clock())
                self._insert_run(connection, plan.run, plan.tasks)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return plan.run

    def persist_run(
        self,
        run: PipelineRun,
        tasks: tuple[PipelineTask, ...],
    ) -> None:
        """Persist a run and its complete task graph in one transaction."""
        if any(task.run_identifier != run.identifier for task in tasks):
            raise ValueError("every task must belong to the persisted run")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert_run(connection, run, tasks)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @classmethod
    def _idempotent_run(
        cls,
        connection: sqlite3.Connection,
        request: CreateRunRequest,
        scoped_key: tuple[str, str],
    ) -> PipelineRun | None:
        rows = connection.execute("SELECT payload FROM runs").fetchall()
        for row in rows:
            existing = pipeline_run_from_json(str(row[0]))
            if cls._scoped_key_for_run(existing) != scoped_key:
                continue
            if existing.request_digest != request.digest:
                namespace, key = scoped_key
                raise RunIdempotencyConflictError(
                    f"idempotency key conflicts: {namespace}/{key}"
                )
            return existing
        return None

    @staticmethod
    def _insert_run(
        connection: sqlite3.Connection,
        run: PipelineRun,
        tasks: tuple[PipelineTask, ...],
    ) -> None:
        connection.execute(
            "INSERT INTO runs(id, payload) VALUES (?, ?)",
            (str(run.identifier), pipeline_run_json(run)),
        )
        connection.executemany(
            "INSERT INTO tasks(id, run_id, state, payload) VALUES (?, ?, ?, ?)",
            (
                (
                    str(task.identifier),
                    str(task.run_identifier),
                    task.state.value,
                    pipeline_task_json(task),
                )
                for task in tasks
            ),
        )
        connection.executemany(
            "INSERT INTO task_dependencies(task_id, dependency_id) VALUES (?, ?)",
            (
                (str(task.identifier), str(dependency))
                for task in tasks
                for dependency in task.dependencies
            ),
        )

    @staticmethod
    def _scoped_key(request: CreateRunRequest) -> tuple[str, str] | None:
        if request.idempotency_key is None:
            return None
        return (request.idempotency_namespace or "default", request.idempotency_key)

    @staticmethod
    def _scoped_key_for_run(run: PipelineRun) -> tuple[str, str] | None:
        if run.idempotency_key is None:
            return None
        return (run.idempotency_namespace or "default", run.idempotency_key)

    def get_run(self, identifier: RunId) -> PipelineRun:
        """Load one durable run snapshot."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM runs WHERE id = ?", (str(identifier),)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown run identifier: {identifier}")
        return pipeline_run_from_json(str(row[0]))

    def list_runs(self) -> tuple[PipelineRun, ...]:
        """Load durable runs in stable identifier order."""
        with self._connection() as connection:
            rows = connection.execute("SELECT payload FROM runs ORDER BY id").fetchall()
        return tuple(pipeline_run_from_json(str(row[0])) for row in rows)

    def get_task(self, identifier: TaskId) -> PipelineTask:
        """Load one durable task snapshot."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM tasks WHERE id = ?", (str(identifier),)
            ).fetchone()
        return pipeline_task_from_json(_require_task_payload(row, identifier))

    def list_tasks(self, run_identifier: RunId) -> tuple[PipelineTask, ...]:
        """Load a run's tasks in stable identifier order."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM tasks WHERE run_id = ? ORDER BY id",
                (str(run_identifier),),
            ).fetchall()
        return tuple(pipeline_task_from_json(str(row[0])) for row in rows)

    def cancel_run(self, identifier: RunId) -> PipelineRun:
        """Atomically cancel a run and all of its cancellable tasks."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                run_row = connection.execute(
                    "SELECT payload FROM runs WHERE id = ?", (str(identifier),)
                ).fetchone()
                run = pipeline_run_from_json(_require_run_payload(run_row, identifier))
                cancelled = (
                    run
                    if run.state is RunState.CANCELLED
                    else apply_run_transition(
                        run,
                        expected=run.state,
                        target=RunState.CANCELLED,
                    )
                )
                task_rows = connection.execute(
                    "SELECT payload FROM tasks WHERE run_id = ? ORDER BY id",
                    (str(identifier),),
                ).fetchall()
                tasks = tuple(
                    replace(
                        task,
                        state=cancelled_task_state(task.state),
                    )
                    for row in task_rows
                    for task in (pipeline_task_from_json(str(row[0])),)
                )
                connection.execute(
                    "UPDATE runs SET payload = ? WHERE id = ?",
                    (pipeline_run_json(cancelled), str(identifier)),
                )
                connection.executemany(
                    "UPDATE tasks SET state = ?, payload = ? WHERE id = ?",
                    (
                        (
                            task.state.value,
                            pipeline_task_json(task),
                            str(task.identifier),
                        )
                        for task in tasks
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return cancelled

    def transition_run(
        self,
        identifier: RunId,
        *,
        expected: RunState,
        target: RunState,
    ) -> PipelineRun:
        """Atomically compare and set one durable run state."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT payload FROM runs WHERE id = ?", (str(identifier),)
                ).fetchone()
                transitioned = apply_run_transition(
                    pipeline_run_from_json(_require_run_payload(row, identifier)),
                    expected=expected,
                    target=target,
                )
                connection.execute(
                    "UPDATE runs SET payload = ? WHERE id = ?",
                    (pipeline_run_json(transitioned), str(identifier)),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return transitioned

    def transition_task(
        self,
        identifier: TaskId,
        *,
        expected: TaskState,
        target: TaskState,
    ) -> PipelineTask:
        """Atomically compare and set one task state."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT payload FROM tasks WHERE id = ?", (str(identifier),)
                ).fetchone()
                transitioned = apply_task_transition(
                    pipeline_task_from_json(_require_task_payload(row, identifier)),
                    expected=expected,
                    target=target,
                )
                connection.execute(
                    "UPDATE tasks SET state = ?, payload = ? WHERE id = ?",
                    (
                        transitioned.state.value,
                        pipeline_task_json(transitioned),
                        str(identifier),
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return transitioned

    def _migrate(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version INTEGER PRIMARY KEY, checksum TEXT, "
                    "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(schema_migrations)"
                    ).fetchall()
                }
                if "checksum" not in columns:
                    connection.execute(
                        "ALTER TABLE schema_migrations ADD COLUMN checksum TEXT"
                    )
                rows = connection.execute(
                    "SELECT version, checksum FROM schema_migrations ORDER BY version"
                ).fetchall()
                version = int(rows[-1][0]) if rows else 0
                _require_supported_schema(version)
                for applied_version, checksum in rows:
                    expected = _require_migration_checksum(
                        int(applied_version),
                        str(checksum) if checksum is not None else None,
                    )
                    if checksum is None:
                        connection.execute(
                            "UPDATE schema_migrations SET checksum = ? "
                            "WHERE version = ?",
                            (expected, int(applied_version)),
                        )
                if version < 1:
                    for statement in _SCHEMA_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, checksum) "
                        "VALUES (1, ?)",
                        (_migration_checksum(),),
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
