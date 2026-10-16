"""Provenance-addressed procedure pipelines."""

from importlib.metadata import version

from .database import PostgresBackend
from .models import (
    ComputeProfile,
    PipelineConfiguration,
    PipelineDefinition,
    load_documents,
)
from .runtime import ExecutionContext, InputSet, Outcome
from .store import Artifact, ArtifactStore

__version__ = version("provium-pipeline")
__all__ = [
    "Artifact",
    "ArtifactStore",
    "ComputeProfile",
    "ExecutionContext",
    "InputSet",
    "Outcome",
    "PipelineConfiguration",
    "PipelineDefinition",
    "PostgresBackend",
    "load_documents",
    "__version__",
]
