"""Local task execution support."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class PreparedProcedure(Protocol):
    """Reusable prepared procedure lifecycle required by the cache."""

    def close(self) -> None:
        """Release the prepared procedure's resources."""


@dataclass(frozen=True, slots=True)
class PreparedProcedureKey:
    """Fingerprints that determine whether prepared state can be reused."""

    procedure: str
    configuration: str
    setup: str


class PreparedProcedureCache[PreparedT: PreparedProcedure]:
    """Own and reuse prepared procedures with identical setup fingerprints."""

    def __init__(self) -> None:
        self._prepared: dict[PreparedProcedureKey, PreparedT] = {}
        self._closed = False

    def get_or_prepare(
        self,
        key: PreparedProcedureKey,
        prepare: Callable[[], PreparedT],
    ) -> PreparedT:
        """Return cached prepared state or create it exactly once."""
        if self._closed:
            raise RuntimeError("prepared procedure cache is closed")
        cached = self._prepared.get(key)
        if cached is not None:
            return cached
        prepared = prepare()
        self._prepared[key] = prepared
        return prepared

    def close(self) -> None:
        """Close every owned prepared procedure exactly once."""
        if self._closed:
            return
        self._closed = True
        prepared_values = tuple(reversed(self._prepared.values()))
        self._prepared.clear()
        failure: BaseException | None = None
        for prepared in prepared_values:
            try:
                prepared.close()
            except BaseException as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure


__all__ = ["PreparedProcedureCache", "PreparedProcedureKey"]
