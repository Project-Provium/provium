"""Provenance-addressed procedure pipelines, loaded on demand."""

from importlib import import_module
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import (
        ExecutionSettings,
        PipelineConfiguration,
        PipelineDefinition,
        load_documents,
    )
    from .publication import FilePublisher, Publisher
    from .requests import CSVSource, ExecutionRequest, RequestSource
    from .runtime import ExecutionContext, InputSet, Outcome
    from .store import Artifact, ArtifactStore

__version__ = version("provium-pipeline")
__all__ = [
    "Artifact",
    "ArtifactStore",
    "ExecutionSettings",
    "CSVSource",
    "ExecutionRequest",
    "FilePublisher",
    "Publisher",
    "RequestSource",
    "ExecutionContext",
    "InputSet",
    "Outcome",
    "PipelineConfiguration",
    "PipelineDefinition",
    "load_documents",
    "__version__",
]

_EXPORTS = {
    "Artifact": ".store",
    "ArtifactStore": ".store",
    "ExecutionSettings": ".models",
    "CSVSource": ".requests",
    "ExecutionRequest": ".requests",
    "FilePublisher": ".publication",
    "Publisher": ".publication",
    "RequestSource": ".requests",
    "ExecutionContext": ".runtime",
    "InputSet": ".runtime",
    "Outcome": ".runtime",
    "PipelineConfiguration": ".models",
    "PipelineDefinition": ".models",
    "load_documents": ".models",
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
