"""Procedure preparation and execution orchestration."""

from collections.abc import Iterable, Mapping, Sequence
from inspect import signature
from typing import Any, Never, cast

from provium.artifact import ArtifactReadBinding, ArtifactWriteBinding
from provium.procedure.config import ProcedureConfig, compose_configuration
from provium.procedure.context import CancellationToken
from provium.procedure.definition import Procedure, ProcedureDefinition
from provium.procedure.io import (
    ProcedureInputs,
    ProcedureOutputs,
    build_procedure_inputs,
    build_procedure_outputs,
)
from provium.procedure.prepared import PreparedProcedure
from provium.procedure.result import ProcedureExecutionResult
from provium.procedure.validation import validate_procedure_configuration

type ReadBindingValue = ArtifactReadBinding[Any] | Sequence[ArtifactReadBinding[Any]]


class ProcedureExecutor:
    """Resolve, validate, and prepare procedure implementations."""

    def prepare[ProcedureT: Procedure[Any, Any, Any, Any]](
        self,
        definition: ProcedureDefinition[ProcedureT],
        *,
        configuration_layers: Iterable[Mapping[str, object]] = (),
        setup_inputs: Mapping[str, ReadBindingValue] | ProcedureInputs | None = None,
    ) -> PreparedProcedure[Any, Any, Any]:
        """Create and set up one reusable procedure instance."""
        procedure_type = definition.resolve()
        configuration = self._prepare_configuration(
            definition,
            configuration_layers,
        )
        inputs = self._prepare_setup_inputs(definition, setup_inputs)
        procedure = self._instantiate(definition, procedure_type)
        return PreparedProcedure(procedure, configuration, inputs)

    def execute[ProcedureT: Procedure[Any, Any, Any, Any]](
        self,
        definition: ProcedureDefinition[ProcedureT],
        *,
        configuration_layers: Iterable[Mapping[str, object]] = (),
        setup_inputs: Mapping[str, ReadBindingValue] | ProcedureInputs | None = None,
        inputs: Mapping[str, ReadBindingValue] | ProcedureInputs | None = None,
        outputs: (
            Mapping[str, ArtifactWriteBinding[Any]] | ProcedureOutputs | None
        ) = None,
        cancellation: CancellationToken | None = None,
    ) -> ProcedureExecutionResult:
        """Prepare, process one invocation, and close the procedure."""
        prepared = self.prepare(
            definition,
            configuration_layers=configuration_layers,
            setup_inputs=setup_inputs,
        )
        try:
            process_inputs = self._prepare_process_inputs(definition, inputs)
            process_outputs = self._prepare_process_outputs(definition, outputs)
            result = prepared.execute(
                inputs=process_inputs,
                outputs=process_outputs,
                cancellation=cancellation,
            )
        except BaseException as error:
            self._close_after_failure(prepared, error)
        prepared.close()
        return result

    @staticmethod
    def _close_after_failure(
        prepared: PreparedProcedure[Any, Any, Any],
        error: BaseException,
    ) -> Never:
        try:
            prepared.close()
        except BaseException as close_error:
            raise error from close_error
        raise error

    @staticmethod
    def _instantiate(
        definition: ProcedureDefinition[Any],
        procedure_type: type[Procedure[Any, Any, Any, Any]],
    ) -> Procedure[Any, Any, Any, Any]:
        try:
            signature(procedure_type).bind()
        except TypeError as error:
            raise TypeError(
                f"procedure {definition.identifier} must be constructible without "
                "arguments"
            ) from error
        return procedure_type()

    @staticmethod
    def _prepare_configuration(
        definition: ProcedureDefinition[Any],
        layers: Iterable[Mapping[str, object]],
    ) -> ProcedureConfig | None:
        values = compose_configuration(layers)
        configuration_type = cast(
            type[ProcedureConfig] | None,
            getattr(definition.resolve_contract(), "configuration"),
        )
        if configuration_type is None:
            if values:
                raise TypeError(
                    f"procedure {definition.identifier} does not accept configuration"
                )
            return None
        return validate_procedure_configuration(
            definition.identifier,
            configuration_type,
            values,
        )

    @staticmethod
    def _prepare_setup_inputs(
        definition: ProcedureDefinition[Any],
        supplied: Mapping[str, ReadBindingValue] | ProcedureInputs | None,
    ) -> ProcedureInputs | None:
        record_type = definition.resolve_contract().SetupInputs
        if record_type is None:
            if supplied is not None and supplied != {}:
                raise TypeError("procedure does not accept setup inputs")
            return None
        if isinstance(supplied, record_type):
            return supplied
        bindings: Mapping[str, object] = (
            dict[str, object]()
            if supplied is None
            else cast(Mapping[str, object], supplied)
        )
        try:
            return build_procedure_inputs(record_type, bindings)
        except (TypeError, ValueError) as error:
            ProcedureExecutor._raise_binding_error(definition, "setup inputs", error)

    @staticmethod
    def _prepare_process_inputs(
        definition: ProcedureDefinition[Any],
        supplied: Mapping[str, ReadBindingValue] | ProcedureInputs | None,
    ) -> ProcedureInputs | None:
        record_type = definition.resolve_contract().Inputs
        if record_type is None:
            if supplied is not None and supplied != {}:
                raise TypeError("procedure does not accept processing inputs")
            return None
        supplied = {} if supplied is None else supplied
        if isinstance(supplied, record_type):
            return supplied
        try:
            return build_procedure_inputs(
                record_type, cast(Mapping[str, object], supplied)
            )
        except (TypeError, ValueError) as error:
            ProcedureExecutor._raise_binding_error(
                definition,
                "processing inputs",
                error,
            )

    @staticmethod
    def _prepare_process_outputs(
        definition: ProcedureDefinition[Any],
        supplied: Mapping[str, ArtifactWriteBinding[Any]] | ProcedureOutputs | None,
    ) -> ProcedureOutputs | None:
        record_type = definition.resolve_contract().Outputs
        if record_type is None:
            if supplied is not None and supplied != {}:
                raise TypeError("procedure does not accept outputs")
            return None
        supplied = {} if supplied is None else supplied
        if isinstance(supplied, record_type):
            return supplied
        try:
            return build_procedure_outputs(
                record_type, cast(Mapping[str, object], supplied)
            )
        except (TypeError, ValueError) as error:
            ProcedureExecutor._raise_binding_error(definition, "outputs", error)

    @staticmethod
    def _raise_binding_error(
        definition: ProcedureDefinition[Any],
        kind: str,
        error: TypeError | ValueError,
    ) -> Never:
        message = f"invalid {kind} for procedure {definition.identifier}: {error}"
        raise type(error)(message) from error


__all__ = ["ProcedureExecutor"]
