# Execution

Execution is split into dispatch planning, durable state transitions, task leasing, invocation construction, core procedure execution, and output acceptance. Each boundary has its own idempotency and recovery rules.

## Why a dispatch is separate from a run

A run is the complete frozen task graph. A dispatch is one authorization to execute a subset now.

Examples of distinct dispatches over the same run include:

- the complete run;
- one record for debugging;
- one downstream node plus missing upstream work;
- only incomplete tasks after interruption;
- only failed tasks during retry.

Changing a dispatch never changes the semantic run snapshot.

## Task selection

`TaskSelection` can filter by:

- all tasks;
- all remaining tasks;
- failed tasks only;
- node identifiers;
- procedure identifiers;
- record keys;
- labels.

Filters combine deterministically. `plan_dispatch()` evaluates them against durable tasks and reusable-task information.

### Dependency policies

`SELECTED_ONLY` rejects a selected task when an unfinished dependency is outside the selection. `INCLUDE_MISSING_UPSTREAM` recursively includes such dependencies in task order.

This distinction is surfaced by the CLI’s `--include-missing-upstream` flag. The strict default prevents an apparently narrow command from silently doing additional work.

## Dispatch creation and idempotency

`DispatchCreationService` reads the frozen run/tasks, calls the planner, and persists a `Dispatch` with:

- a `DispatchId`;
- run ID;
- canonical selection;
- exact task IDs;
- dependency policy;
- `RetryPolicy`;
- creation/transition timestamps;
- dispatch state;
- a run-scoped idempotency key.

Repeating an equivalent create request returns the existing dispatch. Reusing the same identity for a different selection is rejected.

A dispatch with no work can be terminal immediately.

## Run executor lifecycle

`LocalRunExecutor.execute()` is the outer coordinator:

1. create or reuse the dispatch;
2. transition the run from planned to running;
3. transition a nonterminal dispatch from created to running;
4. invoke the configured dispatch runner;
5. mark dispatch success or failure unless another actor already made it terminal;
6. transition the run to the matching terminal state.

State-conflict handling allows cancellation or another terminal writer to win without being overwritten.

## Serial worker loop

`SerialDispatchWorker` executes one claimed task at a time:

1. iterate the dispatch’s frozen task IDs;
2. resolve blocked tasks whose dependencies became terminal;
3. find a ready or retry-wait task;
4. claim an attempt lease;
5. compare-and-set the task to leased;
6. execute the task attempt;
7. transition to succeeded, retry-wait, failed, or cancelled;
8. release the lease;
9. continue until every selected task is terminal.

A blocked task becomes cancelled if an upstream dependency failed/cancelled, or ready when all dependencies succeeded or were reused.

## Attempt leases

`TaskAttemptLease` records task/attempt IDs, attempt number, worker identity, opaque token, start time, expiry, and optional end time.

Lease managers support:

- claim;
- renew with the current token;
- release with the current token;
- recovery of expired leases;
- lookup/history.

`AttemptLeaseManager` is an in-memory reference. `SQLiteAttemptLeaseManager` provides cross-process durability and atomic claims.

The task state and lease record are separate on purpose: task state expresses workflow meaning; the lease expresses temporary ownership.

## Retry policy

A `RetryPolicy` controls maximum attempts, initial backoff, multiplier, and maximum backoff. After an exception, the worker asks the policy for a `RetryDecision` using attempt number, retryability, and current time.

- Retryable work transitions to retry-wait with an eligible time, then back to ready.
- Exhausted or nonretryable work becomes failed and the exception propagates to the dispatch runner.
- Lease release happens in `finally`, including failure paths.

The reference CLI currently classifies task exceptions as retryable and uses a maximum of three attempts. Provider runtimes can supply stricter classification.

## Building a frozen invocation

`FrozenTaskInvocationBuilder` reconstructs one core invocation from durable state:

1. load the run snapshot and locate the task’s record;
2. resolve the installed lightweight procedure definition;
3. verify its contract digest against the frozen task;
4. verify the frozen configuration snapshot against the installed model;
5. resolve record, shared, and upstream output artifact identities from compiled binding plans;
6. look up descriptors and active locations;
7. materialize verified local input files in an attempt-specific workspace;
8. construct typed core read bindings;
9. construct output paths and typed write bindings;
10. calculate the setup fingerprint and `PreparedProcedureKey`;
11. return a `PreparedInvocation` plus owned materializations.

The builder does not reinterpret the original YAML or resolver response.

## Prepared procedure cache

`PreparedProcedureCache` reuses prepared core procedure instances inside one process when procedure contract, configuration snapshot, and setup fingerprint match.

This is different from the computation cache:

- prepared reuse avoids repeating process-local setup;
- computation reuse skips procedure execution because an equivalent accepted result already exists.

Prepared instances are process-local and closed during environment shutdown. They are not serialized across workers.

## Core invocation and output acceptance

The task layer adapts frozen plans into `ProcedureExecutor`/`PreparedProcedure` calls owned by Provium core. Core performs typed reads/writes, transactional artifact finalization, lineage, and cancellation behavior.

`LocalTaskAttemptExecutor` then validates and accepts outputs:

1. invoke the prepared procedure;
2. require exactly the frozen output field set;
3. import each produced finalized `.pa` file into managed storage;
4. preserve absent optional fields as `None`;
5. atomically record one `TaskOutputSet`.

A conflicting second output set is rejected. Downstream tasks resolve node references through this accepted output store.

## Computation reuse

`computation_identity()` builds a versioned canonical document from semantic procedure/configuration/setup/input/output contracts. Operational context is excluded.

`InMemoryComputationCache` demonstrates the required single-flight semantics:

- one task reserves a computation key;
- equivalent tasks observe the reservation;
- only the owner can complete or fail it;
- completion is accepted only if all produced outputs have active locations;
- stale entries are not reused;
- empty successful output contracts can still be reused.

A reused task transitions to the corresponding successful state and exposes the accepted result identities without invoking the procedure.

## Multiprocessing supervision

`MultiprocessSupervisor` maintains one child process per stable slot:

- starts named workers;
- waits for state changes;
- replaces a child that exits while work remains;
- stops replacing workers when the dispatch is terminal;
- requests cancellation and waits for graceful shutdown;
- terminates/join remaining children and preserves the primary error.

Workers coordinate through shared durable stores and leases. The supervisor does not share prepared procedure instances across processes, which preserves setup isolation.

The default CLI uses the serial runner. Applications that need multiple local workers compose the supervisor with a process factory that opens the same SQLite-backed stores.

## Cancellation

Cancellation exists at run/dispatch control and active procedure execution boundaries.

- Durable cancellation transitions prevent new work from being claimed.
- `DispatchCancellationController` can associate an active lease with a core `CancellationToken`.
- Cancellation registration is lease-token scoped so a stale attempt cannot cancel a replacement.
- Workers and supervisors perform cleanup and release materializations/leases.

Committed task outputs remain committed; cancellation does not roll back already accepted task boundaries.

## Crash boundaries

The design treats crashes around each external effect explicitly:

- Before lease: no ownership was acquired.
- After lease, before task transition: claim cleanup releases ownership.
- During procedure execution: core abandons incomplete outputs; lease expires or is released.
- After artifact publication, before output record: republishing the identical artifact is idempotent and orphan handling can identify the unbound location.
- After output record: the task result is authoritative and resumption skips it.
- During dispatch/run terminal transition: compare-and-set plus reread avoids overwriting another terminal actor.

This is why execution has several small services instead of one monolithic “run pipeline” function.
