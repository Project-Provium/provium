"""Registration catalog for procedure definitions."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from provium.procedure.definition import Procedure, ProcedureDefinition


class ProcedureCatalog:
    """Map persistent procedure identifiers to their definitions."""

    def __init__(self) -> None:
        self._definitions: dict[str, ProcedureDefinition[Any]] = {}

    def register[ProcedureT: Procedure[Any, Any, Any, Any]](
        self,
        definition: ProcedureDefinition[ProcedureT],
    ) -> ProcedureDefinition[ProcedureT]:
        """Register and return a procedure definition."""
        if not isinstance(definition, ProcedureDefinition):
            raise TypeError("catalog entries must be ProcedureDefinition instances")
        if definition.identifier in self._definitions:
            raise ValueError(
                f"procedure identifier is already registered: {definition.identifier}"
            )
        self._definitions[definition.identifier] = definition
        return definition

    def resolve(self, identifier: str) -> ProcedureDefinition[Any]:
        """Return the definition registered for an identifier."""
        return self._definitions[identifier]

    @property
    def definitions(self) -> Mapping[str, ProcedureDefinition[Any]]:
        """Expose registered definitions through a read-only mapping."""
        return MappingProxyType(self._definitions)


__all__ = ["ProcedureCatalog"]
