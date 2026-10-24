"""Persistent names for pipeline-independent input fingerprints."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Protocol

from provium_pipeline.input_sets import input_set_hash, require_selector, validate_name


class InputSetIndex(Protocol):
    def register_input_set(
        self, name: str, inputs: dict[str, Any]
    ) -> dict[str, Any]: ...
    def resolve_input_set(
        self, *, name: str | None = None, key: str | None = None
    ) -> dict[str, Any]: ...
    def find_input_sets(self, key: str) -> list[str]: ...
    def unregister_input_sets(
        self, *, name: str | None = None, key: str | None = None
    ) -> list[str]: ...


class LocalInputSetIndex:
    """Transactional index alongside a local artifact store."""

    def __init__(self, root: Path | None) -> None:
        if root is None:
            raise ValueError("an S3 input-set index requires --database")
        self.path = root / "input-sets.sqlite3"
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS input_sets (
                    hash TEXT PRIMARY KEY, inputs TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS input_set_names (
                    name TEXT PRIMARY KEY, hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS input_set_names_hash
                    ON input_set_names(hash);
            """)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            yield connection

    def register_input_set(self, name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        validate_name(name)
        key = input_set_hash(inputs)
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO input_sets VALUES (?, ?)",
                (key, json.dumps(inputs)),
            )
            connection.execute(
                "INSERT OR IGNORE INTO input_set_names VALUES (?, ?)", (name, key)
            )
            row = connection.execute(
                "SELECT hash FROM input_set_names WHERE name = ?", (name,)
            ).fetchone()
            if row[0] != key:
                raise ValueError(
                    f"input-set name already registered to different inputs: {name}"
                )
            record = connection.execute(
                "SELECT inputs FROM input_sets WHERE hash = ?", (key,)
            ).fetchone()
        return {"hash": key, "inputs": json.loads(record[0])}

    def resolve_input_set(
        self, *, name: str | None = None, key: str | None = None
    ) -> dict[str, Any]:
        require_selector(name, key)
        with self.connect() as connection:
            if name is not None:
                row = connection.execute(
                    "SELECT s.hash, s.inputs FROM input_sets s "
                    "JOIN input_set_names n ON n.hash = s.hash WHERE n.name = ?",
                    (name,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT hash, inputs FROM input_sets WHERE hash = ?", (key,)
                ).fetchone()
        if row is None:
            raise ValueError(f"unknown input set: {name if name is not None else key}")
        return {"hash": row[0], "inputs": json.loads(row[1])}

    def find_input_sets(self, key: str) -> list[str]:
        with self.connect() as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM input_set_names WHERE hash = ? ORDER BY name",
                    (key,),
                )
            ]

    def unregister_input_sets(
        self, *, name: str | None = None, key: str | None = None
    ) -> list[str]:
        require_selector(name, key)
        field, value = ("name", name) if name is not None else ("hash", key)
        with self.connect() as connection:
            rows = connection.execute(
                f"DELETE FROM input_set_names WHERE {field} = ? RETURNING name",
                (value,),
            ).fetchall()
        return sorted(row[0] for row in rows)
