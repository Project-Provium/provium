"""Typed, reproducible, idempotent local pipelines for Provium."""

from importlib.metadata import version

from .canonical import canonical_digest, canonical_json, canonical_json_bytes
from .catalog import PipelineCatalog, PipelineCatalogError, PipelineCatalogRegistration
from .compatibility import (
    SUPPORTED_CORE_API_VERSION,
    IncompatibleCoreError,
    require_compatible_core,
)
from .compiler import (
    ArtifactCatalogCollection,
    CatalogResolutionError,
    ProcedureCatalogCollection,
)
from .discovery import (
    PIPELINE_CATALOG_ENTRY_POINT_GROUP,
    PipelineDiscoveryDiagnostic,
    PipelineDiscoveryResult,
    discover_pipeline_catalogs,
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
    "ArtifactCatalogCollection",
    "CatalogResolutionError",
    "ProcedureCatalogCollection",
    "PIPELINE_CATALOG_ENTRY_POINT_GROUP",
    "PipelineCatalogRegistration",
    "PipelineDiscoveryDiagnostic",
    "PipelineDiscoveryResult",
    "discover_pipeline_catalogs",
    "PipelineCatalog",
    "PipelineCatalogError",
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
