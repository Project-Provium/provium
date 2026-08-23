import pytest

from provium_pipeline.compiler import (
    PipelineCompilationDiagnostic,
    PipelineCompilationError,
)
from test.compiler.test_compiler_success import compiler, definition


def test_compiler_reports_unknown_procedure_with_structured_location() -> None:
    source = definition()
    node = source.nodes["transform"].model_copy(update={"uses": "example.MissingV1"})
    invalid = source.model_copy(update={"nodes": {"transform": node}})

    with pytest.raises(PipelineCompilationError) as caught:
        compiler().compile(invalid)

    assert caught.value.diagnostics == (
        PipelineCompilationDiagnostic(
            code="unknown-procedure",
            path="nodes.transform.uses",
            message="procedure 'example.MissingV1' is not registered",
        ),
    )
