"""Durable execution for Provium pipelines."""

from importlib import import_module
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ComputeProfile, PipelineDefinition
    from .runtime import ExecutionContext

__version__ = version("provium-dispatch")
__all__ = ["ComputeProfile", "PipelineDefinition", "ExecutionContext", "__version__"]


def __getattr__(name: str) -> Any:
    modules = {
        "ComputeProfile": ".models",
        "PipelineDefinition": ".models",
        "ExecutionContext": ".runtime",
    }
    if name not in modules:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(modules[name], __name__), name)
    globals()[name] = value
    return value
