# Architecture

The package uses domain models plus narrow protocols and services. Most modules are intentionally small because durable workflow code needs explicit boundaries between planning, persistence, execution, and external systems.

## Layered view

```text
User surfaces
  YAML / JSON / Python builder / catalogs / CLI
                           |
Definition and compilation
  models -> references -> validation -> configuration -> compiled plan
                           |
Reproducible run planning
  frozen inputs -> run fingerprint -> tasks and dependencies
                           |
Execution control
  selection -> dispatch -> transitions -> attempts/leases -> workers
                           |
Procedure bridge
  binding resolution -> materialization -> PreparedProcedure -> output import
                           |
Durability and lifecycle
  SQLite stores / filesystem artifacts / queries / exports / retention / GC
```

Dependencies mostly point downward. Core Provium is adjacent rather than below every layer: definition/compilation consumes lightweight core definitions and contracts; the procedure bridge consumes core prepared-execution and artifact-container APIs.

## Package boundaries

### Definition layer

`provium_pipeline.definition` owns the schema-facing Pydantic models, structured references, YAML/JSON codecs, and typed builder handles. It should know nothing about SQLite, workers, or physical artifact storage.

### Compiler layer

`provium_pipeline.compiler` resolves lightweight catalogs, validates contracts/topology/cardinality, composes configuration layers, and creates immutable compiled models. It must not resolve concrete execution classes merely to validate a graph.

### Run planning layer

`inputs.py`, `input_validation.py`, `input_resolver.py`, `run_models.py`, and `run_creation.py` turn an input source and compiled pipeline into a durable semantic snapshot and task graph.

### Dispatch and execution layer

Dispatch modules own selection, dependency expansion, retry policy, dispatch state, and dispatch persistence. Attempt modules own worker leases. Worker modules claim tasks and coordinate state transitions. Task-execution modules bridge a leased task to Provium core.

### Artifact layer

`provium_pipeline.artifact` defines descriptors, locations, store/index protocols, error categories, filesystem/SQLite adapters, import/materialization behavior, queries, and conformance helpers.

### Read and lifecycle layer

Run queries, graphs, exports, retention, and garbage collection consume durable state without changing the meaning of a run.

### Integration layer

`cli.py`, discovery, catalogs, compatibility checks, and entry-point factories connect the library to installed distributions and the core `provium` executable.

## End-to-end creation flow

```text
source path or catalog identifier
        |
        +-> load PipelineDefinition
        |
configuration layers
        |
        v
PipelineCompiler.compile
  - validate definition model
  - resolve lightweight procedure/artifact definitions
  - validate bindings/cardinality/topology
  - compose and validate node configuration
  - calculate canonical snapshots and digests
        |
        v
resolve or load input records
  - direct NDJSON, named InputSet, or InputRecordResolver
  - validate required/shared/repeated inputs
  - verify logical artifact identities exist
  - freeze RunInputSnapshot
        |
        v
RunCreationService
  - calculate run fingerprint/idempotency key
  - plan one PipelineTask per node and record
  - persist run and tasks atomically
```

Compilation intentionally precedes durable run creation. Invalid definitions or configuration do not leave partial runs. Input records are frozen before persistence so execution cannot drift with external data sources.

## End-to-end execution flow

The graph-backed CLI path is:

```text
pipeline execute
  -> load/discover definition
  -> ResolvedRunCreationService.create
  -> RunCreationService.create_from_snapshot
  -> LocalRunExecutor.execute
  -> DispatchCreationService.create
  -> SerialDispatchWorker.run
  -> claim TaskAttemptLease
  -> FrozenTaskInvocationBuilder.build
  -> LocalTaskAttemptExecutor.execute
  -> core ProcedureExecutor / PreparedProcedure
  -> ArtifactImportService.import_artifact
  -> TaskOutputStore.record
  -> task, dispatch, and run terminal transitions
```

`LocalRunExecutor` owns the outer lifecycle. It creates an idempotent dispatch, transitions the run to running, invokes the configured dispatch runner, records terminal dispatch state, and mirrors that result to the run.

`SerialDispatchWorker` repeatedly examines the dispatch’s exact task identifiers. It resolves blocked dependencies, claims an eligible task, transitions it to leased, executes it, applies retry policy on failure, releases the lease, and stops when every selected task is terminal.

`FrozenTaskInvocationBuilder` reconstructs execution only from the stored run and task snapshots. It verifies the currently installed procedure contract and configuration against frozen digests, resolves record/shared/upstream artifact identities, materializes inputs, creates output bindings, and builds a core prepared invocation.

`LocalTaskAttemptExecutor` accepts a result only when the returned output fields match the frozen contract. Produced outputs are imported into managed storage; absent optional outputs are recorded as `None`; the complete output set is recorded atomically for downstream binding resolution.

## Durability model

The reference local environment places multiple logical stores in one SQLite database:

- execution runs and tasks;
- immutable input sets;
- dispatches;
- attempt leases;
- task output sets;
- artifact descriptors and locations.

Each adapter owns its schema and uses idempotent creation/migration behavior. Compare-and-set transitions include an expected state. Stores reopen across processes, and SQLite transaction boundaries protect multi-row invariants such as run plus task creation.

Physical `.pa` objects are separate from metadata. The filesystem store uses deterministic identity-derived object paths and staging files for safe publication.

## Recovery model

Recovery is built from durable task state and expiring leases rather than checkpointing arbitrary procedure internals.

- A committed task output set is already complete and remains reusable.
- A worker crash leaves at most a leased task and temporary workspace.
- Lease expiry permits another worker to recover ownership.
- Retry policy determines whether a failed attempt returns to ready or becomes terminal.
- Re-executing a run selection can target remaining or failed tasks.
- Publication and output acceptance are idempotent/conflict-aware so a crash around the commit boundary cannot silently replace a different result.

The unit of resumption is a task boundary. Procedures themselves remain transactional core executions.

## Concurrency model

The default CLI composes a serial dispatch worker. The library also supplies:

- thread-safe in-memory caches and stores for local composition/tests;
- SQLite compare-and-set stores usable across processes;
- stable worker identities and lease tokens;
- `MultiprocessSupervisor`, which maintains one child per configured slot, replaces crashed children while work remains, and performs coordinated shutdown.

The supervisor is infrastructure, not an alternate semantic engine: all workers still use the same dispatch, task, lease, and output contracts.

## Extensibility model

Ports are expressed as Python protocols or narrow callable collaborators. Reference adapters are assembled at the environment boundary. Important extension seams include:

- pipeline catalogs;
- input record resolvers;
- artifact stores and indexes;
- execution/dispatch stores;
- task invocation and output import collaborators;
- worker process factories and supervisors.

Entry points discover installed catalogs and storage factories without creating an import dependency from core Provium back to this package.

## Intentional design tradeoffs

### More objects, fewer ambiguous meanings

Run, dispatch, task, and attempt are separate because they have different identity, lifetime, and idempotency rules. Combining them would make partial execution and resumption ambiguous.

### Logical artifacts separate from files

Runs store identities, not transient paths. This adds an index/store layer but permits durable references, multiple locations, verification, and garbage collection.

### Immutable snapshots over live configuration

Storing full validated snapshots costs metadata space but prevents later environment, default, or source-file changes from altering an existing run.

### Explicit state transitions over implicit status inference

State machines add code, but they make concurrency conflicts, retry rules, cancellation, recovery, and audit testable.

### Local-first adapters with provider-neutral contracts

SQLite and filesystem implementations keep the package usable without infrastructure. Protocol boundaries allow later provider distributions to replace adapters without changing the domain model.
