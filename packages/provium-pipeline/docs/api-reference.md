# Module and API reference

This is a responsibility map, not generated signature documentation. It answers where a concept lives and why the module exists. The top-level `provium_pipeline.__all__` is the smallest supported convenience surface. Submodule classes and protocols are typed and intentionally importable, but the distribution is pre-alpha and Phase 14 still includes a public API stability review.

## Top-level package

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline` | Convenience exports, package version, core compatibility check, identifiers, catalogs, inputs, run models, and execution-store contracts. Import validates the installed Provium core API. |
| `provium_pipeline.compatibility` | `SUPPORTED_CORE_API_VERSION`, `require_compatible_core()`, and one actionable `IncompatibleCoreError`. |
| `provium_pipeline.identifiers` | Immutable validated string/UUID-backed identifiers for definitions and runtime entities. |
| `provium_pipeline.canonical` | Re-exports the core canonical JSON bytes/text/digest rules so pipeline documents share one identity algorithm. |

## Definitions and catalogs

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline.definition` | Definition-package exports for models, references, builder, and codecs. |
| `provium_pipeline.definition.models` | Pydantic v2 schema models: pipeline metadata, input scope/cardinality, node definitions, cache policy, outputs, and `PipelineDefinition`. |
| `provium_pipeline.definition.references` | Parses and represents the restricted `$inputs` / `$nodes` reference grammar. |
| `provium_pipeline.definition.builder` | `PipelineBuilder` and typed input/node/output handles that construct the same domain model as YAML/JSON. |
| `provium_pipeline.definition.codec` | YAML/JSON loading, canonical definition documents, source-aware validation diagnostics. |
| `provium_pipeline.plugin` | Plugin-domain package for catalogs and entry-point discovery. |
| `provium_pipeline.plugin.catalog` | `PipelineCatalog`, registration provenance, lazy target/resource resolution, duplicate/identity validation. |
| `provium_pipeline.plugin.discovery` | Installed pipeline-catalog entry-point discovery and structured diagnostics. |

## Compilation

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline.compiler` | Compiler-package exports and catalog collections. |
| `provium_pipeline.compiler.catalogs` | `ArtifactCatalogCollection` and `ProcedureCatalogCollection` resolve unique lightweight core definitions across catalogs. |
| `provium_pipeline.compiler.models` | Immutable `CompiledPipeline`, compiled inputs/nodes/outputs, and ordered binding plans. |
| `provium_pipeline.compiler.configuration` | Pipeline configuration Pydantic models and canonical configuration documents. |
| `provium_pipeline.compiler.resolution` | Ordered configuration layers, canonical layer audit, composition, core configuration snapshots/digests. |
| `provium_pipeline.compiler.validation` | Structured definition/graph/contract/cardinality/configuration validation. |
| `provium_pipeline.compiler.diagnostics` | `PipelineCompilationDiagnostic` and aggregate `PipelineCompilationError`. |
| `provium_pipeline.compiler.compiler` | `PipelineCompiler.compile()`, deterministic topology, canonical definition/semantic digests, and normalized compiled output. |

## Inputs and run planning

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline.input` | Stable input-domain exports spanning models, codecs, resolvers, validation, and local persistence. |
| `provium_pipeline.input.models` | `InputRecord`, immutable `InputSet`, source descriptors, and digest-bearing `RunInputSnapshot`. |
| `provium_pipeline.input.codec` | Strict NDJSON record decoding and line/path diagnostics. |
| `provium_pipeline.input.validation` | Validates frozen record/shared inputs and concrete cardinality against compiled contracts. |
| `provium_pipeline.input.resolver` | Resolver request/context/protocol/catalog/discovery plus `resolve_input_snapshot()`. |
| `provium_pipeline.run.resolved_creation` | `ResolvedRunCreationService` resolves once, verifies artifacts, then creates from the frozen snapshot. |
| `provium_pipeline.run` | Run-domain package for models, creation, queries, transitions, and portable exports. |
| `provium_pipeline.run.models` | Run/task states, create request, run/task/output expectation models, fingerprinting, and deterministic `plan_run()`. |
| `provium_pipeline.run.creation` | `RunCreationService` compiles, freezes, validates, plans, and atomically persists a run. |
| `provium_pipeline.run.transitions` | Legal run-state transitions and transition errors. |

## Execution persistence and codecs

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline.execution` | Execution-domain package for durable state, codecs, attempts, invocation, outputs, and local executors. |
| `provium_pipeline.execution.store` | `ExecutionStore`, `InMemoryExecutionStore`, atomic run/task persistence and compare-and-set transitions. |
| `provium_pipeline.execution.sqlite` | SQLite implementation for durable runs/tasks, schema creation, idempotency, and transitions. |
| `provium_pipeline.execution.codec` | Versioned canonical run/task/compiled/input serialization, decoding, and JSON-domain conversion. |
| `provium_pipeline.input.sqlite` | Durable immutable named input sets and canonical record persistence. |
| `provium_pipeline.execution.outputs` | Complete task output-set models, in-memory/SQLite stores, conflict detection, and upstream reference resolution. |
| `provium_pipeline.execution.task_transitions` | Legal task-state transitions and transition errors. |

## Dispatches, attempts, and workers

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline.dispatch.selection` | General dispatch candidates/selection limits and deterministic selection helpers. |
| `provium_pipeline.dispatch` | Dispatch-domain package for selection, planning, durable storage, transitions, codecs, and workers. |
| `provium_pipeline.dispatch.models` | `Dispatch`, `DispatchState`, `TaskSelection`, dependency/retry policies and retry decisions. |
| `provium_pipeline.dispatch.planning` | Filters durable tasks, expands missing upstream dependencies, reports unsatisfied strict selections. |
| `provium_pipeline.dispatch.creation` | `DispatchCreationService` creates exact run-scoped idempotent dispatches. |
| `provium_pipeline.dispatch.store` | Dispatch protocol and in-memory reference store. |
| `provium_pipeline.dispatch.sqlite` | SQLite dispatch persistence, canonical payloads, transitions, cancellation, and run history. |
| `provium_pipeline.dispatch.transitions` | Legal dispatch-state transitions and conflict errors. |
| `provium_pipeline.dispatch.codec` | Versioned canonical dispatch document/JSON round trips. |
| `provium_pipeline.execution.attempts` | `TaskAttemptLease`, in-memory lease manager, token fencing, expiry/recovery semantics. |
| `provium_pipeline.execution.sqlite_attempts` | Cross-process SQLite lease claims, renewals, releases, recovery, and payload validation. |
| `provium_pipeline.execution.serial` | Minimal serial claim/execute/wait loop independent of pipeline domain details. |
| `provium_pipeline.dispatch.worker` | `SerialDispatchWorker` combines task states, leases, retries, dependencies, and task invocation. |
| `provium_pipeline.execution.run_executor` | `LocalRunExecutor` coordinates dispatch and run outer lifecycle. |
| `provium_pipeline.execution.cancellation` | Lease-scoped active cancellation registration/controller. |
| `provium_pipeline.execution.multiprocessing` | Worker process protocol/factory and crash-replacing `MultiprocessSupervisor`. |

## Task invocation and computation

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline.execution.task_executor` | Prepared invocation data, materialization ownership, typed binding construction, prepared cache, and core invocation helpers. |
| `provium_pipeline.execution.invocation` | `FrozenTaskInvocationBuilder` reconstructs an exact invocation from run/task snapshots and artifact locations. |
| `provium_pipeline.execution.local_attempt` | `LocalTaskAttemptExecutor` invokes one task, imports produced artifacts, preserves optional absence, records the complete output set. |
| `provium_pipeline.cache` | Cache-domain package for computation identity and single-flight durable reuse. |
| `provium_pipeline.cache.computation` | Canonical semantic computation requests, artifact fingerprints, and `ComputationKey` identity. |
| `provium_pipeline.cache.store` | Single-flight reservation, ownership, completion/failure, stale-output validation, and in-memory reference cache. |

## Artifacts

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline.artifact` | Descriptor/location/staging/materialization models, store protocol, error taxonomy, orphan query, cleanup ownership. |
| `provium_pipeline.artifact.index` | Artifact index protocol, in-memory index, registration/location invariants, and identity collisions. |
| `provium_pipeline.artifact.filesystem` | Content-addressed filesystem store with staged atomic publication, verification, materialization, deletion, and orphan listing. |
| `provium_pipeline.artifact.sqlite_index` | SQLite descriptors/locations and active-location lookup. |
| `provium_pipeline.artifact.service` | `ArtifactImportService` verifies finalized core artifacts, publishes, and indexes them. |
| `provium_pipeline.artifact.query` | Run/input-set artifact binding queries with optional physical locations. |
| `provium_pipeline.artifact.plugins` | Artifact store/index entry-point groups and local filesystem/SQLite factories. |
| `provium_pipeline.artifact.conformance` | Reusable adapter conformance expectations/helpers. |

## Queries, exports, graphs, and lifecycle

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline.run.query` | `RunQueryService` composes run, ordered tasks/inputs/outputs, dispatches, and audit timeline from read providers. |
| `provium_pipeline.run.exports` | Stored-state-only resolved configuration, input NDJSON, and complete versioned run bundle exports with sensitive-key redaction rules. |
| `provium_pipeline.graphs` | Deterministic run/task/artifact graph views for inspection and export. |
| `provium_pipeline.lifecycle` | Lifecycle-domain package for retention and garbage collection. |
| `provium_pipeline.lifecycle.retention` | Retention reference model and deterministic in-memory reference index. |
| `provium_pipeline.lifecycle.garbage_collection` | Two-stage retention-aware garbage-collection plans/results and idempotent apply. |

## CLI integration

| Module | Responsibility and principal API |
| --- | --- |
| `provium_pipeline.cli` | Compatibility facade for the CLI application. |
| `provium_pipeline.cli.application` | Core CLI plugin, parser command groups, structured envelope/error handling, local backend, installed environment assembly, and command implementations. |

## Choosing an import

Prefer top-level imports for the stable convenience surface:

```python
from provium_pipeline import InputRecord, RunId, RunInputSnapshot
```

Import subsystem contracts from their defining module when building adapters or custom runtimes:

```python
from provium_pipeline.artifact import ArtifactStore
from provium_pipeline.dispatch.store import DispatchStore
from provium_pipeline.execution.run_executor import LocalRunExecutor
```

Avoid importing underscore-prefixed implementation details. A public name in a submodule is typed and documented here, but pre-1.0 compatibility will be finalized by the Phase 14 API review.

## Identifier families

Definition identifiers validate human-authored canonical strings:

- `PipelineIdentifier`
- `PipelineVersion`
- `PipelineNodeIdentifier`
- `PipelineInputName`
- `PipelineOutputName`
- `InputRecordKey`
- `InputSetIdentifier`
- `StoreIdentifier`

Runtime identifiers wrap UUID values and provide `new()`/`parse()` behavior:

- `RunId`
- `DispatchId`
- `TaskId`
- `AttemptId`

`ComputationKey` represents a canonical semantic digest rather than an application-generated UUID.

## Error handling conventions

Domain errors name the violated boundary: decode, validation, compilation, catalog/discovery, identity collision, location/store, idempotency conflict, illegal transition, lease ownership, dependency selection, invocation mismatch, or output conflict.

Catch a specific domain error when an application can recover at that layer. Allow unexpected exceptions to fail the attempt/dispatch so retry and durable state remain authoritative.
