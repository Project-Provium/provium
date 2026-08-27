# Inputs and runs

The input/run layer is where mutable external information becomes a reproducible workload.

## Input record shape

An `InputRecord` contains:

- an `InputRecordKey` unique within the snapshot;
- a mapping from pipeline input name to an ordered tuple of artifact identities;
- string labels used for selection.

Artifact identities refer to indexed, finalized artifacts. Local source paths are imported before durable run creation and are not retained in the snapshot.

NDJSON is the interchange format for record collections. Each line is validated independently, duplicate keys are rejected, and diagnostics include line/field information.

```json
{"key":"record-001","inputs":{"document":["ARTIFACT_ID"]},"labels":{"split":"evaluation"}}
```

## Shared inputs

`RunInputSnapshot.shared_inputs` maps each shared pipeline input to its ordered identities. Shared values are available to every record task but are stored once in the snapshot.

Use shared inputs for immutable dependencies whose identity is constant across records. Setup inputs still participate in prepared-procedure identity and task computation identity.

## Input source descriptors

Every snapshot records an `InputSourceDescriptor` describing how it was produced. `InputSourceKind` distinguishes direct records, named input sets, and resolver results. Source metadata is canonical JSON and should contain audit information, not transient secrets or live handles.

The source descriptor explains provenance; it does not cause the source to be queried again.

## Named immutable input sets

An `InputSet` gives a reusable name to ordered records, labels, creation time, and digest. `SQLiteInputSetStore` persists sets immutably:

- creating the same identifier with the same content is idempotent;
- redefining an identifier with different content is rejected;
- reopening the database returns the same records and order;
- CLI export produces deterministic NDJSON.

Input sets are useful for reproducible comparisons because two runs can point to precisely the same cohort while changing pipeline configuration.

## Resolver-backed inputs

An `InputRecordResolver` is an async callable discovered by identifier through the `provium.input_record_resolvers` entry-point group. It receives a `ResolveInputRecordsRequest` and `InputResolutionContext` and returns records plus source metadata.

`resolve_input_snapshot()`:

1. invokes the resolver once;
2. rejects duplicate keys or malformed results;
3. freezes the returned records and metadata;
4. calculates the canonical snapshot digest.

`ResolvedRunCreationService` then verifies artifact identities through the artifact index and delegates to `RunCreationService.create_from_snapshot()`.

Resolvers are appropriate for “new or stale objects,” date windows, database queries, or provider APIs. Because the result is frozen, resumption does not depend on the provider returning the same answer later.

## Input validation against a compiled pipeline

`validate_input_snapshot()` checks runtime facts that definition compilation cannot know:

- every required record input is present for every record;
- shared inputs are present in the shared map and not in records;
- record inputs are not incorrectly supplied as shared;
- concrete counts satisfy minimum and maximum cardinality;
- unknown pipeline input names are rejected;
- artifact identities are valid and compatible with the compiled artifact contract when looked up.

This validation happens before the run and tasks are committed.

## Snapshot identity

`RunInputSnapshot.create()` canonicalizes records and shared inputs and calculates a digest. Records are ordered deterministically by record key for identity-sensitive documents. Repeated artifact identities retain caller order.

The digest is part of the run fingerprint. Two syntactically different sources that freeze to the same semantic records can therefore describe the same run inputs.

## Run creation request

`CreateRunRequest` combines:

- the compiled pipeline;
- the frozen input snapshot;
- an optional user idempotency key;
- canonical metadata, including selection audit when created through convenience commands.

`RunCreationService` owns the transaction-like sequence:

1. compile the definition and configuration layers;
2. freeze or accept the input snapshot;
3. validate it against the compiled pipeline and artifact index;
4. calculate run fingerprint/idempotency identity;
5. plan the task graph;
6. atomically persist the run and tasks through `ExecutionStore`.

## Run fingerprint and idempotency

The run fingerprint is based on semantic pipeline identity and frozen input identity, not on an application-generated UUID. It supports deduplication and audit.

An explicit idempotency key allows a caller to safely repeat a create request. Stores return the existing equivalent run or raise `RunIdempotencyConflictError` when the same key is reused for different content.

The `RunId` remains the unique handle for querying and state transitions.

## Task graph planning

`plan_run()` creates one `PipelineTask` for each compiled node and input record. Tasks are ordered by record key and deterministic node topology. Each task freezes:

- run, task, record, and node identifiers;
- procedure identifier and contract digest;
- configuration snapshot;
- setup/input binding plans;
- expected output fields;
- cache policy/computation contract material;
- upstream task IDs;
- initial state.

A node with unfinished dependencies starts blocked. A source-ready node starts ready. The graph is stored rather than recalculated from a mutable definition during execution.

## Run and task states

`RunState` covers planned, running, succeeded, failed, and cancelled lifecycle outcomes. `TaskState` additionally represents blocked, ready, leased, retry-wait, reused, and other task-level outcomes required for recovery.

Transition helpers reject illegal edges. Durable stores require an expected current state, making concurrent or stale writers visible as conflicts.

## Outputs and optional absence

`RunOutputExpectation` maps declared pipeline outputs to the producing node/field for each record. Actual task output sets distinguish:

- a produced artifact identity;
- an absent optional output represented as `None`.

Absence is a completed result, not a missing database row. This distinction is necessary for downstream optional bindings, complete query results, and safe retries.

## Queries versus stored models

`RunQueryService` composes an authoritative read view from narrow providers:

- run snapshot;
- ordered tasks;
- ordered inputs;
- produced/absent outputs;
- dispatch history;
- audit events.

The service keeps read presentation separate from mutation stores. CLI status, task, input, and output commands consume this view.

## Exports

`RunExportService` generates data only from stored state:

- resolved configuration document/JSON;
- deterministic input NDJSON;
- a complete versioned run bundle including run, tasks, inputs, outputs, dispatches, and audit data.

Exports never reread the original definition path, resolver, or configuration file. That property is the practical test of reproducibility.
