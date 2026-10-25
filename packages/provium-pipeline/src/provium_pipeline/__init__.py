"""Provenance-addressed procedure pipelines, loaded on demand."""

from importlib import import_module
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from provium_pipeline.models import (
        ExecutionSettings,
        PipelineConfiguration,
        PipelineDefinition,
        load_documents,
    )
    from provium_pipeline.publication import FilePublisher, Publisher
    from provium_pipeline.requests import CSVSource, ExecutionRequest, RequestSource
    from provium_pipeline.runtime import ExecutionContext, InputSet, Outcome
    from provium_pipeline.store import Artifact, ArtifactStore

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
    "Artifact": "provium_pipeline.store",
    "ArtifactStore": "provium_pipeline.store",
    "ExecutionSettings": "provium_pipeline.models",
    "CSVSource": "provium_pipeline.requests",
    "ExecutionRequest": "provium_pipeline.requests",
    "FilePublisher": "provium_pipeline.publication",
    "Publisher": "provium_pipeline.publication",
    "RequestSource": "provium_pipeline.requests",
    "ExecutionContext": "provium_pipeline.runtime",
    "InputSet": "provium_pipeline.runtime",
    "Outcome": "provium_pipeline.runtime",
    "PipelineConfiguration": "provium_pipeline.models",
    "PipelineDefinition": "provium_pipeline.models",
    "load_documents": "provium_pipeline.models",
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value
