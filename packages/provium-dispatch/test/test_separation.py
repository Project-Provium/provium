"""The basic engine remains usable without durable infrastructure."""

import inspect

from provium_pipeline.models import PipelineDefinition
from provium_pipeline.runtime import ExecutionContext


def test_basic_context_has_no_dispatch_configuration():
    parameters = inspect.signature(ExecutionContext).parameters
    assert "profile" not in parameters
    assert "backend" not in parameters
    assert "queue_group" not in parameters
    assert "coordinator" in parameters


def test_basic_definition_has_no_profile_allowlist():
    assert "profiles" not in inspect.signature(PipelineDefinition).parameters


def test_dispatch_exports_execution_and_profiles():
    from provium_dispatch import ComputeProfile
    from provium_dispatch import ExecutionContext as DispatchContext

    assert issubclass(DispatchContext, ExecutionContext)
    assert ComputeProfile(name="local").name == "local"
