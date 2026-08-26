from pathlib import Path
from typing import Any, cast

import pytest

from provium_pipeline.task_executor import (
    AttemptMaterializations,
    PreparedInvocation,
    PreparedProcedureCache,
    PreparedProcedureKey,
    execute_prepared_invocation,
)


class _Prepared:
    def __init__(self) -> None:
        self.executions: list[dict[str, object]] = []
        self.closed = False

    def execute(self, **kwargs: object) -> object:
        self.executions.append(kwargs)
        return kwargs["inputs"]

    def close(self) -> None:
        self.closed = True


class _Executor:
    def __init__(self, prepared: _Prepared) -> None:
        self.prepared = prepared
        self.preparations: list[tuple[object, tuple[object, ...], object]] = []

    def prepare(
        self,
        definition: object,
        *,
        configuration_layers: tuple[object, ...],
        setup_inputs: object,
    ) -> _Prepared:
        self.preparations.append((definition, configuration_layers, setup_inputs))
        return self.prepared


def _request(*, inputs: object, cancellation: object) -> PreparedInvocation:
    return PreparedInvocation(
        key=PreparedProcedureKey("procedure", "configuration", "setup"),
        definition=cast(Any, object()),
        configuration_layers=({"threshold": 1},),
        setup_inputs={"model": object()},
        inputs=cast(Any, inputs),
        outputs=cast(Any, {"result": object()}),
        cancellation=cast(Any, cancellation),
    )


def test_execute_prepared_invocation_reuses_setup_and_forwards_each_call() -> None:
    prepared = _Prepared()
    executor = _Executor(prepared)
    cache: PreparedProcedureCache[Any] = PreparedProcedureCache()
    first_cancellation = object()
    second_cancellation = object()
    first = _request(inputs={"value": 1}, cancellation=first_cancellation)
    second = _request(inputs={"value": 2}, cancellation=second_cancellation)

    first_result = execute_prepared_invocation(
        first,
        executor=cast(Any, executor),
        cache=cache,
        materializations=AttemptMaterializations(),
    )
    second_result = execute_prepared_invocation(
        second,
        executor=cast(Any, executor),
        cache=cache,
        materializations=AttemptMaterializations(),
    )

    assert first_result is first.inputs
    assert second_result is second.inputs
    assert executor.preparations == [
        (first.definition, first.configuration_layers, first.setup_inputs)
    ]
    assert prepared.executions == [
        {
            "inputs": first.inputs,
            "outputs": first.outputs,
            "cancellation": first_cancellation,
        },
        {
            "inputs": second.inputs,
            "outputs": second.outputs,
            "cancellation": second_cancellation,
        },
    ]
    assert not prepared.closed
    cache.close()
    assert prepared.closed


def test_execute_prepared_invocation_reraises_execution_error_after_cleanup() -> None:
    execution_error = RuntimeError("execution failed")

    class _FailingPrepared(_Prepared):
        def execute(self, **kwargs: object) -> object:
            raise execution_error

    prepared = _FailingPrepared()
    with pytest.raises(RuntimeError, match="execution failed") as caught:
        execute_prepared_invocation(
            _request(inputs={}, cancellation=object()),
            executor=cast(Any, _Executor(prepared)),
            cache=PreparedProcedureCache(),
            materializations=AttemptMaterializations(),
        )

    assert caught.value is execution_error
    assert not prepared.closed


def test_execute_prepared_invocation_preserves_execution_error_over_cleanup(
    tmp_path: Path,
) -> None:
    execution_error = RuntimeError("execution failed")
    cleanup_error = OSError("cleanup failed")

    class _FailingPrepared(_Prepared):
        def execute(self, **kwargs: object) -> object:
            raise execution_error

    prepared = _FailingPrepared()
    executor = _Executor(prepared)
    ownership = AttemptMaterializations(
        remove=lambda _path: (_ for _ in ()).throw(cleanup_error)
    )
    from provium_pipeline.task_executor import (
        MaterializationCleanup,
        MaterializedArtifact,
    )

    ownership.track(
        MaterializedArtifact(
            cast(Any, object()),
            tmp_path / "input.pa",
            MaterializationCleanup.REQUIRED,
        )
    )

    with pytest.raises(RuntimeError, match="execution failed") as caught:
        execute_prepared_invocation(
            _request(inputs={}, cancellation=object()),
            executor=cast(Any, executor),
            cache=PreparedProcedureCache(),
            materializations=ownership,
        )

    assert caught.value is execution_error
    assert caught.value.__cause__ is cleanup_error
    assert not prepared.closed
