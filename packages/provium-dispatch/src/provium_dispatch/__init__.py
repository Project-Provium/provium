"""Durable execution for Provium pipelines."""

from importlib import import_module
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from provium_dispatch.models import ComputeProfile, PipelineDefinition
    from provium_dispatch.runtime import ExecutionContext

__version__ = version("provium-dispatch")
__all__ = ["ComputeProfile", "PipelineDefinition", "ExecutionContext", "__version__"]


def __getattr__(name: str) -> Any:
    modules = {
        "ComputeProfile": "provium_dispatch.models",
        "PipelineDefinition": "provium_dispatch.models",
        "ExecutionContext": "provium_dispatch.runtime",
    }
    if name not in modules:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(modules[name]), name)
    globals()[name] = value
    return value
