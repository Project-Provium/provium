# Concepts and vocabulary

This page explains the objects in `provium-pipeline` by the problem each one solves.

## Provium core versus Provium Pipeline

Provium core knows how to define and execute one typed procedure. It owns artifact formats, artifact readers and writers, procedure contracts, configuration models, prepared procedures, and transactional output finalization.

Provium Pipeline answers the questions that appear when one procedure is no longer enough:

- Which procedures form the graph?
- Which exact artifacts and configuration values were used?
- How is one node repeated across many records?
- What work is safe to retry or resume?
- Which worker owns a task right now?
- Can an equivalent result be reused?
- Where is a finalized artifact stored?
- Why is that artifact still retained?

The package deliberately consumes public core contracts instead of duplicating core execution behavior.

## Definition, compiled pipeline, and run

These three objects look similar but represent different moments.

### Pipeline definition

A `PipelineDefinition` is user intent. It can come from YAML, JSON, a `PipelineBuilder`, an in-memory catalog value, a lazy Python target, or a packaged resource. It names inputs, procedure nodes, bindings, configuration, and exposed outputs.

Definitions may still contain mistakes: missing procedures, wrong artifact types, cycles, bad cardinality, or invalid configuration.

### Compiled pipeline

A `CompiledPipeline` is checked semantic intent. Compilation resolves lightweight artifact and procedure definitions, validates topology and bindings, validates Pydantic configuration, calculates deterministic topological order, and freezes contract/configuration digests.

Compilation does not need to import concrete procedure or artifact implementations. That keeps validation fast and prevents execution-only dependencies from leaking into planning.

### Run

A `PipelineRun` combines one compiled pipeline with one immutable `RunInputSnapshot`. It has an application-generated `RunId`, a run fingerprint, durable state, and a planned task graph.

A run never means “read these files again later.” It means “execute this exact compiled pipeline against these exact logical artifact identities.”

## Inputs, records, input sets, and resolvers

### Input record

An `InputRecord` is one record-scoped unit of work. It has a stable key, named artifact identities, and labels. Each pipeline node normally creates one task for each record.

### Shared input

A shared input is bound once for the whole run, such as a model, vocabulary, or reference dataset. It is stored separately from record inputs in the run snapshot.

### Input set

An `InputSet` is a named immutable cohort. It is useful when multiple runs must use precisely the same membership, such as an evaluation suite.

### Resolver

An `InputRecordResolver` discovers records dynamically from configuration. Resolution happens once; its result is converted into the same frozen snapshot used by direct records and input sets. The resolver is therefore a source of run inputs, not a live dependency of later execution.

## Run, task, dispatch, and attempt

These objects separate durable work from authorization and ownership.

### Task

A `PipelineTask` is one node for one record. It carries:

- its procedure identifier and frozen contract digest;
- its validated configuration snapshot;
- compiled setup and processing binding plans;
- expected output fields;
- upstream task dependencies;
- durable task state.

Tasks belong to a run and remain after any individual execution request ends.

### Dispatch

A `Dispatch` authorizes a selected subset of run tasks to execute now. It stores the selection, dependency policy, exact task identifiers, retry policy, timestamps, and dispatch state.

This separation matters because users may execute a whole run, one node, one record, failed work only, or a resumed subset without changing the run itself.

### Attempt and lease

A task attempt is one worker’s try. A `TaskAttemptLease` contains an ownership token, worker identity, attempt number, expiry, and timestamps.

The lease prevents two workers from accepting the same task concurrently. Expiry allows recovery after a worker dies. Tokens prevent a stale worker from renewing or releasing a newer owner’s lease.

## Dependency policy and selection

A `TaskSelection` describes filters such as nodes, procedures, records, labels, all tasks, remaining tasks, or failed tasks.

`DependencyPolicy.SELECTED_ONLY` requires every selected task’s unfinished dependencies to already be satisfied or selected. `INCLUDE_MISSING_UPSTREAM` recursively expands the dispatch to unfinished prerequisites. The policy is explicit so a convenience flag never silently changes the user’s intended work boundary.

## Artifact identity, descriptor, and location

A logical artifact is not the same thing as a physical file.

- The **artifact identity** names immutable content and provenance according to Provium core.
- A `ManagedArtifactDescriptor` records immutable metadata such as artifact type, digests, size, and creation time.
- An `ArtifactLocation` says where one physical copy lives, which store owns it, whether it is active, and when it was verified.

Separating identity from location allows one logical artifact to have several copies, locations to be replaced or deactivated, and runs to remain meaningful if storage layout changes.

## Materialization and publication

Procedures execute against local finalized `.pa` files. A store may hold those files elsewhere or under a content-addressed layout.

**Materialization** creates a verified local input file for an attempt. **Publication/import** verifies a finalized output, publishes it idempotently into managed storage, registers its descriptor/location, and returns the logical identity that durable task results record.

Temporary materializations belong to an attempt and are cleaned up. Managed published artifacts outlive the attempt.

## Computation identity and cache

A `ComputationKey` describes semantic equivalence rather than run position. It incorporates the procedure contract, validated configuration, exact input artifact fingerprints, setup fingerprint, output contract, and cache policy. Operational details such as run ID, task ID, node name, worker, and workspace are excluded.

That distinction permits cross-run and even cross-pipeline reuse when two tasks ask for the same computation. A cache entry is reusable only while all produced outputs remain active and available. Reservations provide single-flight behavior so equivalent tasks do not perform duplicate work concurrently.

## State machines

Runs, tasks, and dispatches use explicit state machines. Transition helpers and stores require the expected current state, turning concurrent updates into detectable conflicts rather than lost writes.

Typical task progression is:

```text
blocked -> ready -> leased -> succeeded
                    |
                    +-> retry_wait -> ready
                    +-> failed
                    +-> cancelled
```

Not every edge is legal. Persisting explicit state and transition time is the basis for resumption, audit, and conflict detection.

## Retention and garbage collection

Retention answers “why must this logical artifact remain available?” References may come from runs, input sets, accepted outputs, or cache entries.

Garbage collection is a two-stage physical cleanup process:

1. `plan()` freezes old, unreferenced, unretained locations after a grace period.
2. `apply()` rechecks retention and deletes each still-eligible location exactly once.

The recheck closes the race where an artifact becomes retained after planning. This is storage lifecycle management, not Python memory collection.

## Canonical documents and digests

Canonical JSON provides stable ordering and encoding before SHA-256 digests are calculated. It is used for definitions, configuration layers, input snapshots, run fingerprints, task/computation documents, dispatches, and exports.

Canonicalization is why equivalent values produce equivalent identities independent of dictionary insertion order, process, or formatting. Versioned documents allow future readers to reject or migrate incompatible shapes deliberately.
