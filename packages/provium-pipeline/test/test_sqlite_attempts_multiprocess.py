from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from pathlib import Path

import pytest

from provium_pipeline.execution.attempts import LeaseConflictError
from provium_pipeline.identifiers import TaskId
from provium_pipeline.run.models import TaskState
from provium_pipeline.sqlite_attempts import SQLiteAttemptLeaseManager
from provium_pipeline.sqlite_execution_store import SQLiteExecutionStore
from test.test_execution_store import request

_NOW = datetime(2026, 8, 24, tzinfo=UTC)


def _claim_then_exit(database: str, task_identifier: str) -> tuple[int, str]:
    lease = SQLiteAttemptLeaseManager(Path(database)).claim(
        TaskId.parse(task_identifier),
        state=TaskState.READY,
        worker_identity="terminated-worker",
        token="first-token",
        now=_NOW,
        ttl=timedelta(seconds=10),
    )
    return lease.attempt_number, lease.token


def test_sqlite_attempt_leases_validate_and_fence_active_claims(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database, clock=lambda: _NOW)
    run = store.create_run(request(key=None))
    task = store.list_tasks(run.identifier)[0]
    manager = SQLiteAttemptLeaseManager(database)

    with pytest.raises(ValueError, match="busy_timeout_ms"):
        SQLiteAttemptLeaseManager(database, busy_timeout_ms=0)
    with pytest.raises(ValueError, match="not claimable"):
        manager.claim(
            task.identifier,
            state=TaskState.SUCCEEDED,
            worker_identity="worker",
            token="invalid-state",
            now=_NOW,
            ttl=timedelta(seconds=10),
        )
    with pytest.raises(ValueError, match="ttl"):
        manager.claim(
            task.identifier,
            state=TaskState.READY,
            worker_identity="worker",
            token="invalid-ttl",
            now=_NOW,
            ttl=timedelta(0),
        )

    manager.claim(
        task.identifier,
        state=TaskState.READY,
        worker_identity="worker",
        token="active",
        now=_NOW,
        ttl=timedelta(seconds=10),
    )
    with pytest.raises(LeaseConflictError, match="active lease"):
        manager.claim(
            task.identifier,
            state=TaskState.READY,
            worker_identity="worker",
            token="duplicate",
            now=_NOW,
            ttl=timedelta(seconds=10),
        )
    assert manager.recover_expired(_NOW + timedelta(seconds=9)) == ()


def test_sqlite_attempt_leases_renew_release_and_fence_tokens(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database, clock=lambda: _NOW)
    run = store.create_run(request(key=None))
    task = store.list_tasks(run.identifier)[0]
    manager = SQLiteAttemptLeaseManager(database)
    manager.claim(
        task.identifier,
        state=TaskState.READY,
        worker_identity="worker",
        token="owner",
        now=_NOW,
        ttl=timedelta(seconds=10),
    )

    with pytest.raises(LeaseConflictError, match="token"):
        manager.renew(
            task.identifier,
            token="wrong",
            now=_NOW,
            ttl=timedelta(seconds=10),
        )
    with pytest.raises(LeaseConflictError, match="token"):
        manager.release(task.identifier, token="wrong", ended_at=_NOW)
    with pytest.raises(ValueError, match="ttl"):
        manager.renew(
            task.identifier,
            token="owner",
            now=_NOW,
            ttl=timedelta(0),
        )

    renewed = manager.renew(
        task.identifier,
        token="owner",
        now=_NOW + timedelta(seconds=5),
        ttl=timedelta(seconds=10),
    )
    released = manager.release(
        task.identifier,
        token="owner",
        ended_at=_NOW + timedelta(seconds=6),
    )
    second = manager.claim(
        task.identifier,
        state=TaskState.RETRY_WAIT,
        worker_identity="worker",
        token="second",
        now=_NOW + timedelta(seconds=6),
        ttl=timedelta(seconds=1),
    )

    assert renewed.heartbeat_at == _NOW + timedelta(seconds=5)
    assert renewed.expires_at == _NOW + timedelta(seconds=15)
    assert released == replace(renewed, ended_at=_NOW + timedelta(seconds=6))
    assert second.attempt_number == 2
    with pytest.raises(LeaseConflictError, match="expired"):
        manager.renew(
            task.identifier,
            token="second",
            now=_NOW + timedelta(seconds=8),
            ttl=timedelta(seconds=1),
        )


def test_sqlite_attempt_recovery_rolls_back_malformed_payload(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database, clock=lambda: _NOW)
    run = store.create_run(request(key=None))
    task = store.list_tasks(run.identifier)[0]
    manager = SQLiteAttemptLeaseManager(database)
    manager.claim(
        task.identifier,
        state=TaskState.READY,
        worker_identity="worker",
        token="active",
        now=_NOW,
        ttl=timedelta(seconds=10),
    )
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE attempts SET payload = 'not-json'")

    with pytest.raises(json.JSONDecodeError):
        manager.recover_expired(_NOW + timedelta(seconds=11))
    with sqlite3.connect(database) as connection:
        token = connection.execute("SELECT lease_token FROM attempts").fetchone()[0]
    assert token == "active"


def test_sqlite_recovers_lease_after_worker_process_termination(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database, clock=lambda: _NOW)
    run = store.create_run(request(key=None))
    task = store.list_tasks(run.identifier)[0]

    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=get_context("spawn"),
    ) as executor:
        first_number, first_token = executor.submit(
            _claim_then_exit,
            str(database),
            str(task.identifier),
        ).result()

    manager = SQLiteAttemptLeaseManager(database)
    recovered = manager.recover_expired(_NOW + timedelta(seconds=11))
    second = manager.claim(
        task.identifier,
        state=TaskState.RETRY_WAIT,
        worker_identity="recovery-worker",
        token="second-token",
        now=_NOW + timedelta(seconds=11),
        ttl=timedelta(seconds=10),
    )

    assert (first_number, first_token) == (1, "first-token")
    assert len(recovered) == 1
    assert recovered[0].task_identifier == task.identifier
    assert recovered[0].token == "first-token"
    assert second.attempt_number == 2
    assert second.token == "second-token"
