"""Shared record serialization and indexed queries for SQL state backends."""

import json
from typing import Any

from provium_cloud.state.models import Computation, Run

SCHEMA = """
CREATE TABLE IF NOT EXISTS provium_cloud_state (
    namespace TEXT NOT NULL, kind TEXT NOT NULL, identity TEXT NOT NULL,
    status TEXT NOT NULL, input_set TEXT NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (namespace, kind, identity)
)
"""
INDEX = """
CREATE INDEX IF NOT EXISTS provium_cloud_state_status
ON provium_cloud_state (namespace, kind, status, identity)
"""
INPUT_INDEX = """
CREATE INDEX IF NOT EXISTS provium_cloud_state_inputs
ON provium_cloud_state (namespace, kind, input_set, identity)
"""


class SQLTransaction:
    def __init__(
        self, connection: Any, namespace: str, now: float, *, postgres: bool = False
    ) -> None:
        self.connection = connection
        self.namespace = namespace
        self.now = now
        self.postgres = postgres

    def _execute(self, query: str, parameters: tuple[Any, ...]) -> Any:
        return self.connection.execute(
            query.replace("?", "%s") if self.postgres else query, parameters
        )

    def _get(self, kind: str, identity: str) -> str | None:
        row = self._execute(
            "SELECT data FROM provium_cloud_state "
            "WHERE namespace=? AND kind=? AND identity=?",
            (self.namespace, kind, identity),
        ).fetchone()
        return None if row is None else str(row[0])

    def _save(
        self, kind: str, identity: str, data: str, status: str = "", input_set: str = ""
    ) -> None:
        self._execute(
            "INSERT INTO provium_cloud_state VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (namespace, kind, identity) DO UPDATE SET "
            "status=excluded.status, input_set=excluded.input_set, data=excluded.data",
            (self.namespace, kind, identity, status, input_set, data),
        )

    def get_run(self, identity: str) -> Run | None:
        value = self._get("run", identity)
        return None if value is None else Run.model_validate_json(value)

    def save_run(self, run: Run) -> None:
        self._save("run", run.id, run.model_dump_json(), run.status, run.input_set_id)

    def get_computation(self, key: str) -> Computation | None:
        value = self._get("computation", key)
        return None if value is None else Computation.model_validate_json(value)

    def save_computation(self, computation: Computation) -> None:
        active = any(
            g.status in {"queued", "submitting", "submitted", "running"}
            or g.cancellation_pending
            for g in computation.generations
        )
        self._save(
            "computation",
            computation.key,
            computation.model_dump_json(),
            "active" if active else "finished",
        )

    def _page(
        self,
        kind: str,
        after: str,
        limit: int,
        statuses: list[str] | None = None,
        input_set: str | None = None,
    ) -> list[str]:
        if not 1 <= limit <= 1000:
            raise ValueError("page limit must be between 1 and 1000")
        query = (
            "SELECT data FROM provium_cloud_state "
            "WHERE namespace=? AND kind=? AND identity>?"
        )
        parameters: list[Any] = [self.namespace, kind, after]
        if statuses is not None:
            if not statuses:
                return []
            query += " AND status IN (" + ",".join("?" for _ in statuses) + ")"
            parameters.extend(statuses)
        if input_set is not None:
            query += " AND input_set=?"
            parameters.append(input_set)
        query += " ORDER BY identity LIMIT ?"
        parameters.append(limit)
        return [
            str(row[0]) for row in self._execute(query, tuple(parameters)).fetchall()
        ]

    def runs(
        self,
        *,
        statuses: list[str] | None = None,
        input_set_id: str | None = None,
        after: str = "",
        limit: int = 100,
    ) -> list[Run]:
        return [
            Run.model_validate_json(value)
            for value in self._page("run", after, limit, statuses, input_set_id)
        ]

    def computations(
        self, *, active: bool = True, after: str = "", limit: int = 100
    ) -> list[Computation]:
        return [
            Computation.model_validate_json(value)
            for value in self._page(
                "computation", after, limit, ["active"] if active else None
            )
        ]

    def request(self, identity: str) -> tuple[str, str] | None:
        value = self._get("request", identity)
        if value is None:
            return None
        data = json.loads(value)
        return str(data[0]), str(data[1])

    def save_request(self, identity: str, run: str, digest: str) -> None:
        self._save("request", identity, json.dumps([run, digest]))
