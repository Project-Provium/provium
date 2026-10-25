"""Procedure-aware configuration validation."""

from collections.abc import Iterable, Mapping

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from provium.procedure.config import ProcedureConfig


class ProcedureConfigurationError(ValueError):
    """A Pydantic configuration failure annotated with procedure context."""

    def __init__(
        self,
        procedure_identifier: str,
        configuration_type: type[ProcedureConfig],
        validation_error: ValidationError,
        source_layers: Iterable[str] = (),
    ) -> None:
        self.procedure_identifier = procedure_identifier
        self.model_target = (
            f"{configuration_type.__module__}:{configuration_type.__qualname__}"
        )
        self.validation_error = validation_error
        self.source_layers = tuple(source_layers)

        details = "; ".join(
            f"{'.'.join(str(component) for component in detail['loc']) or '<root>'}: "
            f"{detail['msg']} [{detail['type']}]"
            for detail in validation_error.errors()
        )
        sources = (
            f"; sources: {', '.join(self.source_layers)}" if self.source_layers else ""
        )
        super().__init__(
            f"invalid configuration for procedure {procedure_identifier} "
            f"using {self.model_target}: {details}{sources}"
        )

    def errors(self) -> list[ErrorDetails]:
        """Return Pydantic's structured validation details."""
        return self.validation_error.errors()


def validate_procedure_configuration[ConfigT: ProcedureConfig](
    procedure_identifier: str,
    configuration_type: type[ConfigT],
    values: Mapping[str, object],
    *,
    source_layers: Iterable[str] = (),
) -> ConfigT:
    """Validate raw values into the exact configuration subtype."""
    try:
        return configuration_type.model_validate(dict(values))
    except ValidationError as error:
        raise ProcedureConfigurationError(
            procedure_identifier,
            configuration_type,
            error,
            source_layers,
        ) from error


__all__ = [
    "ProcedureConfigurationError",
    "validate_procedure_configuration",
]
