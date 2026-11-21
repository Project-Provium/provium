"""SQLite state for local execution and contract testing."""

import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from provium_cloud.state.backends.sql import INDEX, INPUT_INDEX, SCHEMA, SQLTransaction
from provium_cloud.state.interface import StateTransaction


class SQLiteOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class SQLiteBackend:
    def __init__(self, path: str, *, namespace: str) -> None:
        if path == ":memory:":
            raise ValueError("durable state requires a SQLite file")
        self.path = str(Path(path).resolve())
        self.namespace = namespace
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                connection.execute(SCHEMA)
                connection.execute(INDEX)
                connection.execute(INPUT_INDEX)
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Generator[StateTransaction]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                yield SQLTransaction(connection, self.namespace, time.time())
        finally:
            connection.close()
