"""Durable SQLite-backed task-attempt lease fencing."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from provium_pipeline.attempts import (
    LeaseConflictError,
    TaskAttemptLease,
)
from provium_pipeline.identifiers import AttemptId, TaskId
from provium_pipeline.run_models import TaskState
from provium_pipeline.sqlite_execution_store import SQLiteExecutionStore


class SQLiteAttemptLeaseManager:
    """Persist task-attempt leases so another process can recover them."""

    def __init__(self, database: Path, *, busy_timeout_ms: int = 5_000) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        SQLiteExecutionStore(database, busy_timeout_ms=busy_timeout_ms)
        self._database = database
        self._busy_timeout_ms = busy_timeout_ms

    def claim(
        self,
        task: TaskId,
        *,
        state: TaskState,
        worker_identity: str,
        token: str,
        now: datetime,
        ttl: timedelta,
    ) -> TaskAttemptLease:
        """Atomically claim a ready or retry-eligible durable task."""
        if state not in {TaskState.READY, TaskState.RETRY_WAIT}:
            raise ValueError("task state is not claimable")
        if ttl <= timedelta(0):
            raise ValueError("lease ttl must be positive")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_available(connection, task)
                row = connection.execute(
                    "SELECT COUNT(*) FROM attempts WHERE task_id = ?",
                    (str(task),),
                ).fetchone()
                attempt_number = int(row[0]) + 1
                lease = TaskAttemptLease(
                    task_identifier=task,
                    attempt_number=attempt_number,
                    worker_identity=worker_identity,
                    token=token,
                    started_at=now,
                    heartbeat_at=now,
                    expires_at=now + ttl,
                )
                connection.execute(
                    "INSERT INTO attempts("
                    "id, task_id, lease_token, lease_expires_at, payload"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (
                        str(AttemptId.new()),
                        str(task),
                        token,
                        lease.expires_at.isoformat(),
                        _lease_json(lease),
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return lease

    @staticmethod
    def _require_available(connection: sqlite3.Connection, task: TaskId) -> None:
        active = connection.execute(
            "SELECT 1 FROM attempts WHERE task_id = ? AND lease_token IS NOT NULL",
            (str(task),),
        ).fetchone()
        if active is not None:
            raise LeaseConflictError("task already has an active lease")

    def recover_expired(self, now: datetime) -> tuple[TaskAttemptLease, ...]:
        """Atomically fence and return leases expired at or before ``now``."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    "SELECT id, payload FROM attempts "
                    "WHERE lease_token IS NOT NULL ORDER BY task_id"
                ).fetchall()
                expired = tuple(
                    (str(row[0]), _lease_from_json(str(row[1])))
                    for row in rows
                    if _lease_from_json(str(row[1])).expires_at <= now
                )
                connection.executemany(
                    "UPDATE attempts SET lease_token = NULL, lease_expires_at = NULL "
                    "WHERE id = ?",
                    ((identifier,) for identifier, _ in expired),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return tuple(lease for _, lease in expired)

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


def _lease_json(lease: TaskAttemptLease) -> str:
    return json.dumps(
        {
            "task_identifier": str(lease.task_identifier),
            "attempt_number": lease.attempt_number,
            "worker_identity": lease.worker_identity,
            "token": lease.token,
            "started_at": lease.started_at.isoformat(),
            "heartbeat_at": lease.heartbeat_at.isoformat(),
            "expires_at": lease.expires_at.isoformat(),
            "ended_at": lease.ended_at.isoformat() if lease.ended_at else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _lease_from_json(payload: str) -> TaskAttemptLease:
    value: dict[str, Any] = json.loads(payload)
    ended_at = value["ended_at"]
    return TaskAttemptLease(
        task_identifier=TaskId.parse(value["task_identifier"]),
        attempt_number=value["attempt_number"],
        worker_identity=value["worker_identity"],
        token=value["token"],
        started_at=datetime.fromisoformat(value["started_at"]),
        heartbeat_at=datetime.fromisoformat(value["heartbeat_at"]),
        expires_at=datetime.fromisoformat(value["expires_at"]),
        ended_at=datetime.fromisoformat(ended_at) if ended_at is not None else None,
    )


__all__ = ["SQLiteAttemptLeaseManager"]
