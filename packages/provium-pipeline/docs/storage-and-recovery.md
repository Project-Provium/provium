# Storage and recovery

The local implementation uses SQLite for semantic/operational metadata and the filesystem for finalized artifact objects and attempt workspaces.

## Default layout

Unless `PROVIUM_PIPELINE_DATABASE` is set, the CLI uses:

```text
.provium/
  pipeline.sqlite3
  artifacts/
    objects/
    staging/
    materialized/
  workspaces/
    <task-id>/
      attempt-0001/
```

When the database path is overridden, its parent becomes the data root for artifacts and workspaces.

## Why metadata and objects are separate

SQLite is good at transactional relationships and compare-and-set state. Finalized `.pa` files can be large and need streaming, atomic filesystem operations, or provider-specific object storage.

The database stores logical identities, descriptors, locations, snapshots, state, and relationships. The artifact store owns bytes. A location record connects the two.

## Execution store

`ExecutionStore` is the durable boundary for runs and tasks. It supports atomic run creation, run/task lookup, task listing, and compare-and-set transitions.

Reference implementations:

- `InMemoryExecutionStore` for deterministic unit composition;
- `SQLiteExecutionStore` for local durability and cross-process coordination.

SQLite creation persists the run and all planned tasks in one transaction. Run idempotency keys are unique and conflicting definitions are rejected.

## Dispatch store

`DispatchStore` persists dispatch requests and lifecycle. It supports create, get, list-for-run, transition, and cancellation.

`SQLiteDispatchStore` stores a canonical versioned dispatch document plus indexed state/identity fields. Transition statements require expected state and reject stale writers. Cancellation is idempotent for an already-cancelled dispatch and rejects incompatible terminal outcomes.

## Attempt store

`SQLiteAttemptLeaseManager` provides atomic task ownership across processes. Claims allocate monotonically increasing attempt numbers and reject live competing leases. Renewal/release require the opaque token. Expired leases can be recovered without trusting a dead worker.

Lease JSON is versioned and validated when reopened. Malformed payloads fail the operation rather than partially recovering state.

## Task output store

`SQLiteTaskOutputStore` records one complete `TaskOutputSet` per task. It indexes run, record, node, and field data and resolves upstream references of the form `$nodes.<node>.<field>`.

Recording an identical set is idempotent; recording different results for the same task is a conflict. Optional absence is stored explicitly.

## Input-set store

`SQLiteInputSetStore` stores named immutable cohorts and their canonical records. Identity redefinition is prohibited unless content is equivalent. Listing and export use stable order.

## Artifact index

`SQLiteArtifactIndex` stores managed descriptors and physical locations. Descriptors are immutable by identity. Locations can become inactive without deleting audit history. Active location lookup feeds task materialization, cache validity, and artifact queries.

## SQLite coexistence

All local adapters are designed to share one database path. Each creates its own tables/indexes idempotently and can reopen an existing schema. Connections enable foreign keys and use transactions appropriate to claims and state transitions.

The adapters avoid keeping one global connection so processes can open their own connection safely. Application code should not share a live `sqlite3.Connection` across worker processes.

## Atomicity boundaries

Important atomic operations are:

- run plus all tasks creation;
- compare-and-set run/task/dispatch transition;
- attempt claim/renew/release;
- complete task output-set record;
- descriptor/location registration.

Artifact publication is atomic at the store boundary but cannot be in the same SQLite transaction as filesystem replacement. The design therefore makes publication idempotent and treats a published-but-unbound object as an orphan candidate.

## Recovery after interruption

On restart:

1. reopen the same database and artifact root;
2. inspect durable run/task/dispatch state;
3. recover or wait out expired leases;
4. create a dispatch selecting remaining or failed tasks;
5. rebuild invocations from frozen snapshots;
6. skip already accepted task outputs;
7. continue at task boundaries.

No in-memory scheduler queue is authoritative. Durable tasks and dispatch membership are the work ledger.

## Crash scenarios

### Worker dies while leased

The task remains leased until recovery determines the lease expired. Another worker claims a new attempt with a new token and attempt number. A stale worker cannot release the new lease.

### Process dies during materialization or execution

Attempt-owned workspace files may remain. They are not accepted outputs. A later attempt uses a different attempt-number directory and can clean stale workspaces operationally.

### Process dies after output publication

The content-addressed object may exist without a task output binding. Retrying publication is idempotent. Orphan scanning plus retention/GC can remove the unbound copy after a grace period.

### Process dies after output record

The accepted `TaskOutputSet` is authoritative. The task can be reconciled to success and downstream bindings resolve the stored identities.

### Competing terminal transitions

Compare-and-set detects the conflict. Coordinators reread terminal state and do not overwrite cancellation or another completed actor.

## Backups

For a consistent local backup while idle, copy the SQLite database and artifact root together. During active writes, use SQLite’s backup API or a transactionally consistent database snapshot, then copy all active artifact locations referenced by that snapshot.

A database without its artifact objects preserves audit but cannot materialize data. Artifact objects without the database can be inspected by core Provium but lose managed relationships.

## Moving a local workspace

The filesystem store’s locators are interpreted relative to its configured root. Move the database and artifact root together and configure the new `PROVIUM_PIPELINE_DATABASE` parent consistently. Do not edit locator JSON manually.

## Corruption behavior

Materialization and import verify core artifact containers and descriptor fields. Corrupt bytes raise categorized errors and are not silently accepted. Operators can deactivate a bad location while retaining other copies of the same logical identity.

Malformed versioned SQLite payloads fail decoding. They should be treated as storage corruption requiring backup/repair, not as absent state.

## Schema evolution

Current adapters create/reopen versioned-compatible schemas idempotently. Release hardening is responsible for reproducibility and migration review before public stability. Provider adapters should use explicit expand/backfill/contract migrations rather than destructive startup rewrites.

## Operational limits

SQLite is the reference local adapter, not a distributed database. It is appropriate for one host and multiple coordinated local processes. Network filesystems, high write concurrency, remote worker fleets, and provider-specific claims should use dedicated adapters with equivalent contracts.
