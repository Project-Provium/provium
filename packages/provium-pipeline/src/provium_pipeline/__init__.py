"""Typed, reproducible, idempotent local pipelines for Provium."""

from importlib.metadata import version

from .canonical import canonical_digest, canonical_json, canonical_json_bytes
from .compatibility import (
    SUPPORTED_CORE_API_VERSION,
    IncompatibleCoreError,
    require_compatible_core,
)
from .identifiers import (
    AttemptId,
    ComputationKey,
    DispatchId,
    InputRecordKey,
    InputSetIdentifier,
    PipelineIdentifier,
    PipelineInputName,
    PipelineNodeIdentifier,
    PipelineOutputName,
    PipelineVersion,
    RunId,
    StoreIdentifier,
    TaskId,
)

__version__ = version("provium-pipeline")

require_compatible_core()

__all__ = [
    "AttemptId",
    "ComputationKey",
    "DispatchId",
    "IncompatibleCoreError",
    "InputRecordKey",
    "InputSetIdentifier",
    "PipelineIdentifier",
    "PipelineInputName",
    "PipelineNodeIdentifier",
    "PipelineOutputName",
    "PipelineVersion",
    "RunId",
    "StoreIdentifier",
    "TaskId",
    "SUPPORTED_CORE_API_VERSION",
    "__version__",
    "canonical_digest",
    "canonical_json",
    "canonical_json_bytes",
    "require_compatible_core",
]
