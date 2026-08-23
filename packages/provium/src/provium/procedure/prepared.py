"""Reusable prepared procedure instances."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Never, cast
from uuid import uuid4

from provium.artifact import ArtifactReadBinding, ArtifactWriteBinding
from provium.provenance import ProcedureRecord
from provium.session import PersistentSession, Session, session

from .authorization import authorize_bindings
from .config import ConfigurationSnapshot, ProcedureConfig
from .context import CancellationToken, ProcedureProcessContext, ProcedureSetupContext
from .definition import Procedure, ProcedureDefinition
from .execution import ProcedureExecutionSession
from .io import (
    ProcedureInputs,
    ProcedureOutputs,
    build_procedure_inputs,
    build_procedure_outputs,
)
from .result import ProcedureExecutionResult


class _SetupTemporaryDirectory:
    def __init__(self) -> None:
        self._directory = TemporaryDirectory(prefix="provium-setup-")
        self.path = Path(self._directory.name)

    def close(self) -> None:
        self._directory.cleanup()


class PreparedProcedure[
    ConfigT: ProcedureConfig | None,
    InputsT: ProcedureInputs | None,
    OutputsT: ProcedureOutputs | None,
]:
    """Own one configured procedure instance and its reusable setup state."""

    def __init__[SetupInputsT: ProcedureInputs | None](
        self,
        procedure: Procedure[ConfigT, SetupInputsT, InputsT, OutputsT],
        configuration: ConfigT,
        setup_inputs: SetupInputsT,
    ) -> None:
        self._procedure: Procedure[ConfigT, Any, InputsT, OutputsT] = procedure
        self._configuration = configuration
        self._state_lock = Lock()
        self._active = False
        self._closed = False
        self._setup_session = PersistentSession()
        self._setup_temporary_directory = _SetupTemporaryDirectory()
        self._setup_bindings = self._input_bindings(setup_inputs)
        self._setup_identities: dict[int, str] = {}
        try:
            self._run_setup(setup_inputs)
        except BaseException as error:
            self._close_setup_after_failure(error)

    def _run_setup(self, setup_inputs: ProcedureInputs | None) -> None:
        with self._setup_session:
            self._setup_session.manage(self._setup_temporary_directory)
            with authorize_bindings(self._setup_bindings, {}, {}):
                self._setup_identities = self._register_inputs(self._setup_bindings)
            with authorize_bindings(
                self._setup_bindings,
                {},
                {},
                input_identities=self._setup_identities,
                allow_binding_creation=False,
            ):
                self._procedure.setup(
                    ProcedureSetupContext(
                        temporary_directory=self._setup_temporary_directory.path
                    ),
                    self._configuration,
                    setup_inputs,
                )

    @property
    def configuration(self) -> ConfigT:
        """Return the configuration shared by setup and every execution."""
        return self._configuration

    def execute(
        self,
        *,
        inputs: Mapping[
            str, ArtifactReadBinding[Any] | Sequence[ArtifactReadBinding[Any]]
        ]
        | InputsT = None,
        outputs: Mapping[str, ArtifactWriteBinding[Any]] | OutputsT = None,
        cancellation: CancellationToken | None = None,
    ) -> ProcedureExecutionResult:
        """Process one invocation on the prepared instance."""
        self._begin_execution()
        try:
            prepared_inputs = self._prepare_inputs(inputs)
            prepared_outputs = self._prepare_outputs(outputs)
            token = cancellation or CancellationToken()
            token.raise_if_cancelled()
            with TemporaryDirectory(prefix="provium-process-") as directory:
                context = ProcedureProcessContext(token, Path(directory))
                with self._setup_session:
                    return self._execute_active(
                        prepared_inputs,
                        prepared_outputs,
                        context,
                    )
        finally:
            self._finish_execution()

    def _prepare_inputs(
        self,
        supplied: Mapping[
            str, ArtifactReadBinding[Any] | Sequence[ArtifactReadBinding[Any]]
        ]
        | InputsT,
    ) -> InputsT:
        if isinstance(supplied, ProcedureInputs):
            return cast(InputsT, supplied)
        definition = self._procedure.definition
        record_type = definition.resolve_contract().Inputs
        if record_type is None:
            if supplied is not None and supplied != {}:
                raise TypeError("procedure does not accept processing inputs")
            return cast(InputsT, None)
        supplied = {} if supplied is None else supplied
        try:
            return cast(
                InputsT,
                build_procedure_inputs(record_type, supplied),
            )
        except (TypeError, ValueError) as error:
            raise type(error)(
                f"invalid processing inputs for procedure "
                f"{definition.identifier}: {error}"
            ) from error

    def _prepare_outputs(
        self,
        supplied: Mapping[str, ArtifactWriteBinding[Any]] | OutputsT,
    ) -> OutputsT:
        if isinstance(supplied, ProcedureOutputs):
            return cast(OutputsT, supplied)
        definition = self._procedure.definition
        record_type = definition.resolve_contract().Outputs
        if record_type is None:
            if supplied is not None and supplied != {}:
                raise TypeError("procedure does not accept outputs")
            return cast(OutputsT, None)
        supplied = {} if supplied is None else supplied
        try:
            return cast(
                OutputsT,
                build_procedure_outputs(
                    record_type,
                    supplied,
                ),
            )
        except (TypeError, ValueError) as error:
            raise type(error)(
                f"invalid outputs for procedure {definition.identifier}: {error}"
            ) from error

    def _execute_active(
        self,
        inputs: InputsT,
        outputs: OutputsT,
        context: ProcedureProcessContext,
    ) -> ProcedureExecutionResult:
        input_bindings = self._input_bindings(inputs)
        output_bindings = self._output_bindings(outputs)
        if output_bindings:
            return self._execute_with_outputs(
                inputs,
                outputs,
                input_bindings,
                output_bindings,
                context,
            )
        if input_bindings:
            return self._execute_without_outputs(
                inputs, outputs, input_bindings, context
            )
        with authorize_bindings((), {}, {}, allow_binding_creation=False):
            self._process(inputs, outputs, context)
        return self._execution_result(self._setup_session)

    def _execute_with_outputs(
        self,
        inputs: InputsT,
        outputs: OutputsT,
        input_bindings: tuple[ArtifactReadBinding[Any], ...],
        output_bindings: dict[str, ArtifactWriteBinding[Any]],
        context: ProcedureProcessContext,
    ) -> ProcedureExecutionResult:
        procedure = self._procedure_record()
        self._validate_output_destinations(output_bindings, procedure.name)
        with ProcedureExecutionSession(procedure) as execution:
            with authorize_bindings(input_bindings, {}, {}):
                identities = self._register_inputs(input_bindings)
            writers = execution.stage_outputs(output_bindings)
            with authorize_bindings(
                input_bindings,
                output_bindings,
                writers,
                input_identities=identities,
                allow_binding_creation=False,
            ):
                self._process(inputs, outputs, context)
        from dataclasses import replace

        from provium.artifact.inspection import inspect_finalized_artifact
        from provium.procedure.result import ProcedureOutputResult

        result = cast(ProcedureExecutionResult, execution.result)
        output_results = {
            name: ProcedureOutputResult(
                path=binding.path.resolve(),
                reference=result.outputs.get(name),
                inspection=(
                    inspect_finalized_artifact(binding.path)
                    if name in result.outputs
                    else None
                ),
            )
            for name, binding in output_bindings.items()
        }
        return replace(result, output_results=output_results)

    def _execute_without_outputs(
        self,
        inputs: InputsT,
        outputs: OutputsT,
        input_bindings: tuple[ArtifactReadBinding[Any], ...],
        context: ProcedureProcessContext,
    ) -> ProcedureExecutionResult:
        with session() as active:
            with authorize_bindings(input_bindings, {}, {}):
                identities = self._register_inputs(input_bindings)
            with authorize_bindings(
                input_bindings,
                {},
                {},
                input_identities=identities,
                allow_binding_creation=False,
            ):
                self._process(inputs, outputs, context)
            return self._execution_result(active)

    def _process(
        self,
        inputs: InputsT,
        outputs: OutputsT,
        context: ProcedureProcessContext,
    ) -> None:
        context.cancellation.raise_if_cancelled()
        self._procedure.process(
            context,
            self._configuration,
            inputs,
            outputs,
        )

    @staticmethod
    def _input_bindings(
        inputs: ProcedureInputs | None,
    ) -> tuple[ArtifactReadBinding[Any], ...]:
        if inputs is None:
            return ()
        values = cast(dict[str, object], object.__getattribute__(inputs, "_values"))
        bindings: list[ArtifactReadBinding[Any]] = []
        for value in values.values():
            if isinstance(value, tuple):
                bindings.extend(cast(tuple[ArtifactReadBinding[Any], ...], value))
            elif value is not None:
                bindings.append(cast(ArtifactReadBinding[Any], value))
        return tuple(bindings)

    def _output_bindings(
        self,
        outputs: ProcedureOutputs | None,
    ) -> dict[str, ArtifactWriteBinding[Any]]:
        if outputs is None:
            return {}
        values = cast(dict[str, object], object.__getattribute__(outputs, "_values"))
        bindings: dict[str, ArtifactWriteBinding[Any]] = {}
        for name, value in values.items():
            if isinstance(value, ArtifactWriteBinding):
                bindings[name] = cast(ArtifactWriteBinding[Any], value)
        return bindings

    def _validate_output_destinations(
        self,
        bindings: dict[str, ArtifactWriteBinding[Any]],
        procedure_identifier: str,
    ) -> None:
        fields_by_destination: dict[Path, str] = {}
        for name, binding in bindings.items():
            destination = binding.path.resolve()
            conflicting_name = fields_by_destination.get(destination)
            if conflicting_name is not None:
                raise ValueError(
                    f"invalid outputs for procedure {procedure_identifier}: "
                    f"fields {conflicting_name} and {name} use the same destination"
                )
            fields_by_destination[destination] = name

    @staticmethod
    def _register_inputs(
        bindings: tuple[ArtifactReadBinding[Any], ...],
    ) -> dict[int, str]:
        identities: dict[int, str] = {}
        for binding in bindings:
            reader = binding.open()
            identities[id(binding)] = reader.identity
            reader.close()
        return identities

    def _procedure_record(self) -> ProcedureRecord:
        record = self._optional_procedure_record()
        if record is None:
            raise TypeError("artifact-producing procedure must declare a definition")
        return record

    def _optional_procedure_record(self) -> ProcedureRecord | None:
        definition = getattr(self._procedure, "definition", None)
        if not isinstance(definition, ProcedureDefinition):
            return None
        if self._configuration is None:
            return ProcedureRecord(
                definition.identifier,
                definition.resolve_contract().metadata.digest,
            )
        snapshot = ConfigurationSnapshot.from_configuration(self._configuration)
        return ProcedureRecord(
            definition.identifier,
            definition.resolve_contract().metadata.digest,
            snapshot.value,
            "pydantic-v2",
        )

    def _execution_result(self, active: Session) -> ProcedureExecutionResult:
        return ProcedureExecutionResult(
            str(uuid4()),
            self._optional_procedure_record(),
            tuple(record.reference for record in active.inputs),
            lineage=active.input_lineage,
        )

    def close(self) -> None:
        """Close the prepared instance exactly once."""
        if not self._begin_close():
            return
        close_error = self._close_procedure()
        self._close_setup_resources(close_error)

    def _close_procedure(self) -> BaseException | None:
        close_error: BaseException | None = None
        try:
            with self._setup_session:
                with authorize_bindings(
                    self._setup_bindings,
                    {},
                    {},
                    input_identities=self._setup_identities,
                    allow_binding_creation=False,
                ):
                    self._procedure.close()
        except BaseException as error:
            close_error = error
        return close_error

    def _close_setup_resources(self, close_error: BaseException | None) -> None:
        try:
            self._setup_session.close()
        except BaseException as setup_close_error:
            if close_error is not None:
                raise close_error from setup_close_error
            raise
        if close_error is not None:
            raise close_error

    def _close_setup_after_failure(self, error: BaseException) -> Never:
        try:
            self._setup_session.close()
        except BaseException as close_error:
            raise error from close_error
        raise error

    def _begin_execution(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("prepared procedure is closed")
            if self._active:
                raise RuntimeError("prepared procedure is already executing")
            self._active = True

    def _finish_execution(self) -> None:
        with self._state_lock:
            self._active = False

    def _begin_close(self) -> bool:
        with self._state_lock:
            if self._closed:
                return False
            if self._active:
                raise RuntimeError("cannot close an executing prepared procedure")
            self._closed = True
            return True


__all__ = ["PreparedProcedure"]
