"""Context-managed imperative procedure executions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any, Self
from uuid import uuid4

from provium.artifact import ArtifactReadBinding, ArtifactWriteBinding, ArtifactWriter
from provium.procedure.authorization import authorize_bindings
from provium.procedure.execution import ProcedureExecutionSession
from provium.procedure.result import ProcedureExecutionResult
from provium.provenance import ProcedureRecord
from provium.session import Session, session


class ImperativeProcedure:
    """Identify a procedure used through direct context-managed artifact access."""

    def __init__(self, identifier: str, contract_digest: str) -> None:
        self.record = ProcedureRecord(identifier, contract_digest)

    def execute(
        self,
        *,
        inputs: Iterable[ArtifactReadBinding[Any]] = (),
        outputs: Mapping[str, ArtifactWriteBinding[Any]] | None = None,
    ) -> ImperativeProcedureExecution:
        """Create a single-use imperative execution context."""
        input_bindings = tuple(inputs)
        output_bindings = {} if outputs is None else dict(outputs)
        self._validate_bindings(input_bindings, output_bindings)
        return ImperativeProcedureExecution(
            self.record,
            input_bindings,
            output_bindings,
        )

    @staticmethod
    def _validate_bindings(
        inputs: tuple[ArtifactReadBinding[Any], ...],
        outputs: Mapping[str, ArtifactWriteBinding[Any]],
    ) -> None:
        if any(not isinstance(binding, ArtifactReadBinding) for binding in inputs):
            raise TypeError("imperative procedure inputs must contain read bindings")
        for name, binding in outputs.items():
            if not isinstance(name, str):
                raise TypeError("imperative procedure output names must be strings")
            if not name.strip():
                raise ValueError("imperative procedure output names must be nonempty")
            if not isinstance(binding, ArtifactWriteBinding):
                raise TypeError(
                    "imperative procedure outputs must contain write bindings"
                )


class ImperativeProcedureExecution:
    """Authorize explicit bindings and publish their provenance on success."""

    def __init__(
        self,
        procedure: ProcedureRecord,
        inputs: tuple[ArtifactReadBinding[Any], ...],
        outputs: Mapping[str, ArtifactWriteBinding[Any]],
    ) -> None:
        self._procedure = procedure
        self._inputs = inputs
        self._outputs = dict(outputs)
        self._parent: Session | None = None
        self._execution: ProcedureExecutionSession | None = None
        self._read_session: Session | None = None
        self._authorization: AbstractContextManager[None] | None = None
        self._result: ProcedureExecutionResult | None = None
        self._used = False

    @property
    def result(self) -> ProcedureExecutionResult | None:
        """Return the completed result, or ``None`` while execution is active."""
        return self._result

    def __enter__(self) -> Self:
        if self._used:
            raise RuntimeError(
                "imperative procedure execution has already been entered"
            )
        self._used = True
        self._parent = session()
        self._parent.__enter__()
        try:
            self._enter_execution_scope()
            identities = self._register_inputs()
            writers = self._stage_outputs()
            self._authorization = authorize_bindings(
                self._inputs,
                self._outputs,
                writers,
                input_identities=identities,
            )
            self._authorization.__enter__()
        except BaseException as error:
            self._abort_enter(error)
        return self

    def _enter_execution_scope(self) -> None:
        if self._outputs:
            self._execution = ProcedureExecutionSession(self._procedure)
            self._execution.__enter__()
            return
        self._read_session = session()
        self._read_session.__enter__()

    def _stage_outputs(self) -> Mapping[str, ArtifactWriter]:
        if self._execution is None:
            return {}
        return self._execution.stage_outputs(self._outputs)

    def _register_inputs(self) -> dict[int, str]:
        identities: dict[int, str] = {}
        with authorize_bindings(self._inputs, {}, {}):
            for binding in self._inputs:
                reader = binding.open()
                identities[id(binding)] = reader.identity
                reader.close()
        return identities

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._authorization is None or self._parent is None:
            raise RuntimeError("imperative procedure execution is not active")
        authorization = self._authorization
        parent = self._parent
        self._authorization = None
        self._parent = None
        authorization.__exit__(exc_type, exc_value, traceback)
        try:
            self._exit_execution_scope(exc_type, exc_value, traceback)
        finally:
            parent.__exit__(exc_type, exc_value, traceback)

    def _exit_execution_scope(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._execution is not None:
            self._execution.__exit__(exc_type, exc_value, traceback)
            self._result = self._execution.result
            return
        assert self._read_session is not None
        self._read_session.__exit__(exc_type, exc_value, traceback)
        if exc_type is None:
            self._result = ProcedureExecutionResult(
                str(uuid4()),
                self._procedure,
                tuple(record.reference for record in self._read_session.inputs),
                lineage=self._read_session.input_lineage,
            )

    def _abort_enter(self, error: BaseException) -> None:
        try:
            self._exit_execution_scope(type(error), error, error.__traceback__)
        finally:
            assert self._parent is not None
            self._parent.__exit__(type(error), error, error.__traceback__)
        raise error


__all__ = ["ImperativeProcedure", "ImperativeProcedureExecution"]
