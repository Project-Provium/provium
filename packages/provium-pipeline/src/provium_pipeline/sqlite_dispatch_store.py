import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from provium_pipeline.dispatch_codec import dispatch_from_json, dispatch_json
from provium_pipeline.dispatch_models import Dispatch, DispatchState
from provium_pipeline.dispatch_transitions import apply_dispatch_transition
from provium_pipeline.identifiers import DispatchId, RunId


class SQLiteDispatchStore:
    """Persist dispatches with transactional run-scoped idempotency."""

    def __init__(
        self,
        database: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(UTC))
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dispatches (
                    identifier TEXT PRIMARY KEY,
                    run_identifier TEXT NOT NULL,
                    idempotency_key TEXT,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE(run_identifier, idempotency_key)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS dispatches_run_order "
                "ON dispatches(run_identifier, created_at, identifier)"
            )

    def create(self, dispatch: Dispatch) -> Dispatch:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._by_identifier(connection, dispatch)
            if existing is not None:
                return existing
            existing = self._by_idempotency_key(connection, dispatch)
            if existing is not None:
                return existing
            self._insert(connection, dispatch)
        return dispatch

    def _by_identifier(
        self,
        connection: sqlite3.Connection,
        dispatch: Dispatch,
    ) -> Dispatch | None:
        row = connection.execute(
            "SELECT payload FROM dispatches WHERE identifier = ?",
            (str(dispatch.identifier),),
        ).fetchone()
        if row is None:
            return None
        existing = dispatch_from_json(cast(str, row[0]))
        if existing != dispatch:
            raise ValueError(
                f"immutable dispatch identifier already exists: {dispatch.identifier}"
            )
        return existing

    def _by_idempotency_key(
        self,
        connection: sqlite3.Connection,
        dispatch: Dispatch,
    ) -> Dispatch | None:
        if dispatch.idempotency_key is None:
            return None
        row = connection.execute(
            "SELECT payload FROM dispatches "
            "WHERE run_identifier = ? AND idempotency_key = ?",
            (str(dispatch.run_identifier), dispatch.idempotency_key),
        ).fetchone()
        if row is None:
            return None
        existing = dispatch_from_json(cast(str, row[0]))
        if not _same_idempotent_intent(existing, dispatch):
            raise ValueError(
                f"dispatch idempotency key conflicts: "
                f"{dispatch.run_identifier}/{dispatch.idempotency_key}"
            )
        return existing

    def _insert(
        self,
        connection: sqlite3.Connection,
        dispatch: Dispatch,
    ) -> None:
        connection.execute(
            "INSERT INTO dispatches("
            "identifier, run_identifier, idempotency_key, created_at, payload"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                str(dispatch.identifier),
                str(dispatch.run_identifier),
                dispatch.idempotency_key,
                dispatch.created_at.astimezone(UTC).isoformat(
                    timespec="microseconds"
                ),
                dispatch_json(dispatch),
            ),
        )

    def transition(
        self,
        identifier: DispatchId,
        *,
        expected: DispatchState,
        target: DispatchState,
    ) -> Dispatch:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM dispatches WHERE identifier = ?",
                (str(identifier),),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown dispatch: {identifier}")
            transitioned = apply_dispatch_transition(
                dispatch_from_json(cast(str, row[0])),
                expected=expected,
                target=target,
                transitioned_at=self._clock(),
            )
            connection.execute(
                "UPDATE dispatches SET payload = ? WHERE identifier = ?",
                (dispatch_json(transitioned), str(identifier)),
            )
        return transitioned

    def get(self, identifier: DispatchId) -> Dispatch:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM dispatches WHERE identifier = ?",
                (str(identifier),),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown dispatch: {identifier}")
        return dispatch_from_json(cast(str, row[0]))

    def list_for_run(self, run_identifier: RunId) -> tuple[Dispatch, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM dispatches WHERE run_identifier = ? "
                "ORDER BY created_at, identifier",
                (str(run_identifier),),
            ).fetchall()
        return tuple(dispatch_from_json(cast(str, row[0])) for row in rows)

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _same_idempotent_intent(existing: Dispatch, proposed: Dispatch) -> bool:
    return (
        existing.run_identifier == proposed.run_identifier
        and existing.selection == proposed.selection
        and existing.dependency_policy == proposed.dependency_policy
        and existing.retry_policy == proposed.retry_policy
        and existing.idempotency_key == proposed.idempotency_key
    )


__all__ = ["SQLiteDispatchStore"]
