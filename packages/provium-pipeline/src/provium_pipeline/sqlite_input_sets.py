"""Durable immutable input-set persistence."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from provium import JsonValue, canonical_digest, canonical_json

from .identifiers import InputRecordKey, InputSetIdentifier
from .inputs import InputRecord, InputSet


class SQLiteInputSetStore:
    """Persist immutable named input sets in one local SQLite database."""

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
                CREATE TABLE IF NOT EXISTS input_sets (
                    identity TEXT PRIMARY KEY,
                    identifier TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL
                )
                """
            )

    def create(
        self,
        *,
        identifier: InputSetIdentifier,
        records: Sequence[InputRecord],
        metadata: Mapping[str, JsonValue],
    ) -> InputSet:
        ordered = tuple(sorted(records, key=lambda item: str(item.key)))
        keys = tuple(str(item.key) for item in ordered)
        if len(set(keys)) != len(keys):
            raise ValueError("input set record keys must be unique")
        content: dict[str, JsonValue] = {
            "identifier": str(identifier),
            "metadata": dict(metadata),
            "records": [_record_document(item) for item in ordered],
        }
        digest = canonical_digest(content)
        value = InputSet(
            identity=digest,
            identifier=identifier,
            records=ordered,
            digest=digest,
            created_at=self._clock(),
            metadata=metadata,
        )
        payload = canonical_json(_input_set_document(value))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT identity, payload FROM input_sets WHERE identifier = ?",
                (str(identifier),),
            ).fetchone()
            if row is not None:
                existing = _input_set_from_json(cast(str, row[1]))
                if cast(str, row[0]) != digest:
                    raise ValueError(
                        f"immutable input set identifier already exists: {identifier}"
                    )
                return existing
            connection.execute(
                "INSERT INTO input_sets(identity, identifier, payload) "
                "VALUES (?, ?, ?)",
                (digest, str(identifier), payload),
            )
        return value

    def get(self, identity: str | InputSetIdentifier) -> InputSet:
        value = str(identity)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM input_sets WHERE identity = ? OR identifier = ?",
                (value, value),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown input set: {value}")
        return _input_set_from_json(cast(str, row[0]))

    def list(self) -> tuple[InputSet, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM input_sets ORDER BY identifier"
            ).fetchall()
        return tuple(_input_set_from_json(cast(str, row[0])) for row in rows)

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _record_document(record: InputRecord) -> dict[str, JsonValue]:
    return {
        "key": str(record.key),
        "inputs": {name: list(values) for name, values in record.inputs.items()},
        "labels": dict(record.labels),
    }


def _input_set_document(value: InputSet) -> dict[str, JsonValue]:
    return {
        "schema": "provium.input-set/v1",
        "input_set": {
            "created_at": value.created_at.isoformat(),
            "digest": value.digest,
            "identifier": str(value.identifier),
            "identity": value.identity,
            "metadata": dict(value.metadata),
        },
        "records": [_record_document(record) for record in value.records],
    }


def _input_set_from_json(payload: str) -> InputSet:
    document = cast(dict[str, Any], json.loads(payload))
    header = cast(dict[str, Any], document["input_set"])
    records = tuple(
        InputRecord(
            key=InputRecordKey(cast(str, item["key"])),
            inputs=cast(dict[str, list[str]], item["inputs"]),
            labels=cast(dict[str, JsonValue], item["labels"]),
        )
        for item in cast(list[dict[str, Any]], document["records"])
    )
    return InputSet(
        identity=cast(str, header["identity"]),
        identifier=InputSetIdentifier(cast(str, header["identifier"])),
        records=records,
        digest=cast(str, header["digest"]),
        created_at=datetime.fromisoformat(cast(str, header["created_at"])),
        metadata=cast(dict[str, JsonValue], header["metadata"]),
    )


__all__ = ["SQLiteInputSetStore"]
