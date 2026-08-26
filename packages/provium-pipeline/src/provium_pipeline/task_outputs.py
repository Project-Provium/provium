"""Accepted task-output mappings and upstream reference resolution."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType

from .identifiers import InputRecordKey, RunId, TaskId


class TaskOutputConflictError(RuntimeError):
    """A task already has a different accepted output mapping."""


@dataclass(frozen=True, slots=True)
class TaskOutputSet:
    """All accepted output fields for one completed task."""

    task_identifier: TaskId
    run_identifier: RunId
    record_key: InputRecordKey
    node_identifier: str
    outputs: Mapping[str, str | None]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "outputs",
            MappingProxyType(dict(sorted(self.outputs.items()))),
        )


class InMemoryTaskOutputStore:
    """Thread-safe reference implementation for accepted task outputs."""

    def __init__(self) -> None:
        self._by_task: dict[TaskId, TaskOutputSet] = {}
        self._lock = RLock()

    def record(self, outputs: TaskOutputSet) -> TaskOutputSet:
        with self._lock:
            existing = self._by_task.get(outputs.task_identifier)
            if existing is None:
                self._by_task[outputs.task_identifier] = outputs
                return outputs
            if existing != outputs:
                raise TaskOutputConflictError(
                    f"task {outputs.task_identifier} outputs are already recorded"
                )
            return existing

    def resolve(
        self,
        run_identifier: RunId,
        record_key: InputRecordKey,
        reference: str,
    ) -> tuple[str, ...]:
        parts = reference.removeprefix("$nodes.").split(".")
        if not reference.startswith("$nodes.") or len(parts) != 2:
            return ()
        node_identifier, field = parts
        with self._lock:
            for output_set in self._by_task.values():
                if (
                    output_set.run_identifier == run_identifier
                    and output_set.record_key == record_key
                    and output_set.node_identifier == node_identifier
                ):
                    identity = output_set.outputs.get(field)
                    return () if identity is None else (identity,)
        return ()

    def list_for_run(self, run_identifier: RunId) -> tuple[TaskOutputSet, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        outputs
                        for outputs in self._by_task.values()
                        if outputs.run_identifier == run_identifier
                    ),
                    key=lambda outputs: str(outputs.task_identifier),
                )
            )


_TASK_OUTPUT_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_output_sets (
    task_identifier TEXT PRIMARY KEY,
    run_identifier TEXT NOT NULL,
    record_key TEXT NOT NULL,
    node_identifier TEXT NOT NULL,
    outputs_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_output_sets_run
    ON task_output_sets (run_identifier, task_identifier);
CREATE UNIQUE INDEX IF NOT EXISTS task_output_sets_reference
    ON task_output_sets (run_identifier, record_key, node_identifier);
"""


class SQLiteTaskOutputStore:
    """SQLite-backed accepted task-output mappings."""

    def __init__(self, database: Path) -> None:
        self._database = database.resolve()
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(_TASK_OUTPUT_SCHEMA)

    def record(self, outputs: TaskOutputSet) -> TaskOutputSet:
        payload = self._payload(outputs)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM task_output_sets WHERE task_identifier = ?",
                (str(outputs.task_identifier),),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO task_output_sets (
                        task_identifier, run_identifier, record_key,
                        node_identifier, outputs_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    payload,
                )
                return outputs
            existing = self._from_row(row)
            if existing != outputs:
                raise TaskOutputConflictError(
                    f"task {outputs.task_identifier} outputs are already recorded"
                )
            return existing

    def resolve(
        self,
        run_identifier: RunId,
        record_key: InputRecordKey,
        reference: str,
    ) -> tuple[str, ...]:
        parts = reference.removeprefix("$nodes.").split(".")
        if not reference.startswith("$nodes.") or len(parts) != 2:
            return ()
        node_identifier, field = parts
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM task_output_sets
                WHERE run_identifier = ? AND record_key = ? AND node_identifier = ?
                """,
                (str(run_identifier), str(record_key), node_identifier),
            ).fetchone()
        if row is None:
            return ()
        identity = self._from_row(row).outputs.get(field)
        return () if identity is None else (identity,)

    def list_for_run(self, run_identifier: RunId) -> tuple[TaskOutputSet, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM task_output_sets
                WHERE run_identifier = ? ORDER BY task_identifier
                """,
                (str(run_identifier),),
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _payload(outputs: TaskOutputSet) -> tuple[str, str, str, str, str]:
        return (
            str(outputs.task_identifier),
            str(outputs.run_identifier),
            str(outputs.record_key),
            outputs.node_identifier,
            json.dumps(dict(outputs.outputs), sort_keys=True, separators=(",", ":")),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> TaskOutputSet:
        values = json.loads(row["outputs_json"])
        return TaskOutputSet(
            task_identifier=TaskId.parse(row["task_identifier"]),
            run_identifier=RunId.parse(row["run_identifier"]),
            record_key=InputRecordKey(row["record_key"]),
            node_identifier=row["node_identifier"],
            outputs=values,
        )


__all__ = [
    "InMemoryTaskOutputStore",
    "SQLiteTaskOutputStore",
    "TaskOutputConflictError",
    "TaskOutputSet",
]
