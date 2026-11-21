"""PostgreSQL state with serialized, short scheduling decisions per namespace."""

import hashlib
from collections.abc import Generator
from contextlib import contextmanager
from importlib import import_module
from typing import Any

from pydantic import BaseModel, ConfigDict

from provium_cloud.state.backends.sql import INDEX, INPUT_INDEX, SCHEMA, SQLTransaction
from provium_cloud.state.interface import StateTransaction


class PostgresOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_env: str


class PostgresBackend:
    def __init__(self, connection: str, *, namespace: str) -> None:
        self.driver: Any = import_module("psycopg")
        self.connection = connection
        self.namespace = namespace
        self.lock = int.from_bytes(
            hashlib.sha256(namespace.encode()).digest()[:8], signed=True
        )
        with self.driver.connect(connection) as database:
            database.execute("SELECT pg_advisory_xact_lock(726819431)")
            database.execute(SCHEMA)
            database.execute(INDEX)
            database.execute(INPUT_INDEX)

    @contextmanager
    def transaction(self) -> Generator[StateTransaction]:
        with self.driver.connect(self.connection) as connection:
            # shortcut: namespace lock; partition when measured contention warrants it.
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (self.lock,))
            now = float(
                connection.execute(
                    "SELECT EXTRACT(EPOCH FROM clock_timestamp())"
                ).fetchone()[0]
            )
            yield SQLTransaction(connection, self.namespace, now, postgres=True)
