"""Typed, reproducible, idempotent local pipelines for Provium."""

import sys
from importlib import import_module
from importlib.metadata import version

from .canonical import canonical_digest, canonical_json, canonical_json_bytes
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
from .plugin.catalog import (
    PipelineCatalog,
    PipelineCatalogError,
    PipelineCatalogRegistration,
)
from .plugin.discovery import (
    PIPELINE_CATALOG_ENTRY_POINT_GROUP,
    PipelineDiscoveryDiagnostic,
    PipelineDiscoveryResult,
    discover_pipeline_catalogs,
)

__version__ = version("provium-pipeline")

from .execution.store import (
    ExecutionStore,
    InMemoryExecutionStore,
    RunIdempotencyConflictError,
)
from .input import (
    InputRecordDecodeError,
    InputRecordResolver,
    InputResolutionContext,
    InputValidationError,
    ResolveInputRecordsRequest,
    load_input_records_ndjson,
    resolve_input_snapshot,
    validate_input_snapshot,
)
from .input.models import (
    InputRecord,
    InputSet,
    InputSourceDescriptor,
    InputSourceKind,
    RunInputSnapshot,
)
from .run.models import (
    CreateRunRequest,
    PipelineRun,
    PipelineTask,
    RunOutputExpectation,
    RunPlan,
    RunState,
    TaskState,
    plan_run,
    run_fingerprint,
)

require_compatible_core()

_LEGACY_MODULE_ALIASES = {
    "attempts": "execution.attempts",
    "cancellation": "execution.cancellation",
    "execution_codec": "execution.codec",
    "execution_store": "execution.store",
    "sqlite_execution_store": "execution.sqlite",
    "sqlite_attempts": "execution.sqlite_attempts",
    "task_executor": "execution.task_executor",
    "task_invocation_builder": "execution.invocation",
    "local_task_attempt": "execution.local_attempt",
    "task_outputs": "execution.outputs",
    "task_transitions": "execution.task_transitions",
    "run_execution": "execution.run_executor",
    "serial": "execution.serial",
    "multiprocessing": "execution.multiprocessing",
    "computation": "cache.computation",
    "computation_cache": "cache.store",
    "retention": "lifecycle.retention",
    "gc": "lifecycle.garbage_collection",
    "catalog": "plugin.catalog",
    "discovery": "plugin.discovery",
    "dispatch_models": "dispatch.models",
    "dispatch_codec": "dispatch.codec",
    "dispatch_planner": "dispatch.planning",
    "dispatch_creation": "dispatch.creation",
    "dispatch_store": "dispatch.store",
    "sqlite_dispatch_store": "dispatch.sqlite",
    "dispatch_worker": "dispatch.worker",
    "dispatch_transitions": "dispatch.transitions",
    "run_models": "run.models",
    "run_creation": "run.creation",
    "resolved_run_creation": "run.resolved_creation",
    "run_query": "run.query",
    "run_transitions": "run.transitions",
    "exports": "run.exports",
}
for _legacy_name, _current_name in _LEGACY_MODULE_ALIASES.items():
    sys.modules[f"{__name__}.{_legacy_name}"] = import_module(
        f".{_current_name}", package=__name__
    )

__all__ = [
    "CreateRunRequest",
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
    "ExecutionStore",
    "InMemoryExecutionStore",
    "InputRecord",
    "InputRecordDecodeError",
    "InputRecordKey",
    "InputRecordResolver",
    "InputResolutionContext",
    "InputSet",
    "InputSetIdentifier",
    "InputSourceDescriptor",
    "InputSourceKind",
    "InputValidationError",
    "PipelineIdentifier",
    "PipelineInputName",
    "PipelineNodeIdentifier",
    "PipelineOutputName",
    "PipelineVersion",
    "PipelineRun",
    "PipelineTask",
    "ResolveInputRecordsRequest",
    "RunId",
    "RunIdempotencyConflictError",
    "RunOutputExpectation",
    "RunPlan",
    "RunState",
    "RunInputSnapshot",
    "StoreIdentifier",
    "TaskId",
    "TaskState",
    "SUPPORTED_CORE_API_VERSION",
    "__version__",
    "canonical_digest",
    "canonical_json",
    "canonical_json_bytes",
    "load_input_records_ndjson",
    "require_compatible_core",
    "resolve_input_snapshot",
    "plan_run",
    "run_fingerprint",
    "validate_input_snapshot",
]
