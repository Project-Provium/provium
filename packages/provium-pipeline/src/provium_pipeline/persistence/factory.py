"""Load only the selected persistence implementation."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .interface import PersistenceBackend


class Backend(Enum):
    Postgres = "postgres"


def open_backend(
    connection: str, *, backend: Backend = Backend.Postgres
) -> PersistenceBackend:
    if backend is not Backend.Postgres:
        raise TypeError("backend must be a Backend member")
    from .backends.postgres import PostgresBackend

    return PostgresBackend(connection)
