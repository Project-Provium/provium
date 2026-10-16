# Provium Pipeline Requirements

Status: implemented specification for the initial local and PostgreSQL-backed package.

This document specifies the current design. It supersedes earlier proposals. See [Pipeline API Examples](pipeline-api-examples.md) for CLI and Python usage. These requirements govern supported behavior.

## 1. Purpose and scope

Provium Pipeline connects standard Provium procedures into a graph and processes existing artifacts with provenance-based reuse. It supports direct local execution without a database and persistent queued execution backed by PostgreSQL. The same execution model must work in environments launched locally, by Slurm, or by AWS Batch.

Keep the initial implementation small: static directed acyclic graphs, one resident configured procedure node per execution context, filesystem or S3 storage, and worker counts as the only resource setting. Hardware allocation belongs to the deployment environment.

Initial scope includes branching, joining, multiple named inputs/outputs, and final outputs that directly reference initial inputs. Dynamic graphs, loops, cross-InputSet aggregation, streaming artifacts, distributed training collectives, shared model memory across processes, autoscaling, and artifact garbage collection are deferred.

## 2. Public concepts

| Concept | Meaning |
| --- | --- |
| Pipeline Definition | Named input and output artifact definitions, plus allowed compute profile names. |
| Pipeline Configuration | A named graph mapping inputs through configured procedures to final outputs. |
| Procedure node | A configured use of a standard Provium procedure; different nodes may use the same implementation. |
| InputSet | An unnamed, immutable mapping of pipeline input names to existing artifact references. |
| Compute Profile | Named execution settings, separate from the graph: worker counts and optional node time limits. |
| Artifact Store | Locates, saves, and loads artifacts using provenance-based addressing. |
| InputSetQueue | Durable priority queue, progress tracking, and coordination for registered pipelines. It has no compute profile. |
| Execution Context | Coordinates scheduling, procedure lifecycle, workers, publication, progress, and callbacks. |
| Computation | One procedure invocation with resolved semantic configuration and bound artifacts. Equivalent computations share a key across pipelines. |
| Computation lease | Renewable, provenance-specific ownership of execution/publication in a shared store namespace. |

InputSets always retain their original named inputs. Intermediate artifacts do not become replacement InputSets. Queue IDs, execution IDs, and attempt IDs are internal tracking identities, not user-assigned task names or provenance keys.

Initial artifacts must already exist. Importing raw files and creating initial artifacts are outside the pipeline API. A CLI reference may locate an existing artifact file; it must not implicitly convert a raw file into one.

## 3. Definitions and graph configuration

### Definition and procedures

A Pipeline Definition specifies named initial inputs and final outputs using standard core artifact definitions, and the names of permitted compute profiles. It requires no separate public name/version or new contract hierarchy.

Procedures use the standard core Provium contracts, configuration, preparation, invocation, and cleanup lifecycle. They have no knowledge of pipelines, queues, tags, or compute profiles. The pipeline binds their existing named inputs, setup inputs, and outputs without introducing pipeline-specific wrappers.

Every result-affecting dependency must be represented by semantic configuration or an artifact binding, including model weights, lookup tables, external data revisions, and relevant seeds. Prepared instances must not leak InputSet-specific state between invocations.

### YAML documents

Support YAML and programmatic construction of the same validated models. Every YAML document has an explicit `kind` and nonempty `name`:

- `kind: PipelineConfiguration` identifies a graph.
- `kind: ComputeProfile` identifies one compute profile.

Accept repeatable `-f <path>` options and multi-document YAML separated by `---`. Read files and documents in supplied order. Ignore empty documents; reject unknown kinds, duplicate mapping keys, unknown structural fields, and duplicate `(kind, name)` objects in one batch. Do not merge documents implicitly or evaluate arbitrary YAML objects/expressions.

A Pipeline Configuration contains its kind, name, procedure nodes, semantic procedure configuration, optional node `tags`, named input/setup-input bindings, and final output mappings. Queue/database connections, store paths, compute settings, and scheduling settings are supplied separately.

Bindings use complete references:

```text
$inputs.<input_name>
$nodes.<node_name>.<output_name>
```

The same syntax applies to invocation inputs, setup inputs, and final outputs. It is not string interpolation or expression evaluation. Ordinary configuration strings are not graph references. Names used as reference/selector segments must be nonempty and cannot contain dots.

Tags are optional lists on configured nodes. They select execution settings and eligibility; they never affect provenance.

### Validation and snapshots

Validate before claiming work or loading expensive procedure resources:

- Resolve procedures, versions, semantic configuration/defaults, and all references.
- Reject cycles, missing required bindings, incompatible artifact definitions, and missing final outputs.
- Preserve core contract semantics for optional inputs and collections; do not invent new absence semantics.
- Require one source per input unless the core contract accepts a collection; preserve meaningful collection order.
- Report errors with the responsible node, binding, or configuration field.
- Execute only nodes contributing to declared outputs; diagnose unused nodes rather than silently running them.
- Validate a selected profile against both available profiles and the definition's allowed names.

Resolve an immutable definition/configuration snapshot for a run. Queued InputSets pin that snapshot. Editing YAML or changing registry state must not silently change already queued computations.

## 4. Compute profiles and selectors

Worker counts are positive integers; zero, negative values, and booleans are invalid. The default is **1**. Every procedure's preparation, invocation, and normal cleanup run in a child worker process with its own prepared instance. There is no same-thread procedure execution mode.

Worker counts apply per context and control concurrency, not CPU, memory, GPU allocation, or system resource checks. No hardware request fields are supported.

Profiles contain ordered `workers` rules, optional ordered per-node `soft_time_limits` and `hard_time_limits` rules, and optional global `soft_time_limit` and `hard_time_limit` fields:

```yaml
kind: ComputeProfile
name: cluster
soft_time_limit: 3600
workers:
  - default: 1
  - tags.model: 2
  - pipelines.text-embedding.tags.large-model: 1
  - pipelines.text-embedding.nodes.encode: 4
soft_time_limits:
  - tags.model: 300
  - pipelines.text-embedding.nodes.encode: 60
```

Each rule is a single-entry mapping. Supported profile selectors are `default`, `tags.<tag>`, `pipelines.<pipeline>.tags.<tag>`, and `pipelines.<pipeline>.nodes.<node>`.

Resolve each setting independently, from least to most specific:

1. Global default.
2. Matching tags, whether global or pipeline-qualified.
3. Exact configured node.

Later rules win at equal specificity. A later less-specific rule cannot override a more-specific rule. Rules for other pipelines are ignored. Validate syntax at registration; resolve applicable targets when executing the selected pipeline. Profiles may reference pipelines registered later.

CLI selectors are scoped to the active pipeline:

- Repeatable `--only nodes.<name>` or `--only tags.<tag>`: union of eligible nodes; omission allows all nodes.
- Repeatable `--workers default=<count>`, `--workers tags.<tag>=<count>`, or `--workers nodes.<name>=<count>`.
- Optional `--profile <name>`: selects a loaded or registered profile.

Append CLI worker rules after profile rules, retaining specificity precedence. Report unknown nodes and unmatched tags clearly. Worker rules do not select nodes for execution. Loading a profile does not select it automatically; without a selected profile, CLI settings apply over the one-worker default.

Values in `soft_time_limits` and `hard_time_limits` are measured in seconds and must be positive and finite; omission means unlimited, and a null rule clears a less-specific limit. They belong in profiles and use the active-time semantics below.

## 5. Provenance and cache reuse

### Required identity semantics

Use one core provenance calculation for expected store lookup, execution leases, and verification of recorded ancestry. The semantic configuration hash covers only resolved procedure configuration. The complete computation provenance additionally covers:

- Procedure semantic identity/version and contract.
- Named invocation and setup artifact bindings.
- Each input artifact's type, stable producing provenance, and content digest.

An output is distinguished by its standard contract output name and type. Its expected producing provenance is computable before execution and excludes its own content digest. When used downstream, both its producing provenance and actual content digest participate.

Exclude pipeline names, configured node names, unrelated branches, tags, UUIDs, queue/run/attempt IDs, timestamps, paths, profiles, workers, time limits, and other operational settings. Equivalent processing in different pipelines must share cache results and computation leases.

Canonical encoding must be deterministic and versioned. Mapping order is irrelevant; meaningful sequence order is retained. Resolve defaults consistently and reject ambiguous/unsupported values and non-finite numbers. Procedure authors must change semantic revisions when behavior changes; source-change detection is not automatic.

A reusable result must have the expected producing provenance and artifact types, valid content, all required outputs present, and no active publication. File existence alone is insufficient. Access errors, corruption, and transport failures must not be treated as cache misses.

### Forced recomputation

Support forcing all nodes or selected nodes/tags, using `--force all`, `--force nodes.<name>`, and `--force tags.<tag>`. Force bypasses cache reuse for the selected computation, executes it, and replaces the committed result with its newly validated outputs. Do not compare old and new content as a condition of replacement.

Force does not change the producing provenance and does not bypass leases or execution eligibility. A selected computation is forced at most once within a run even when multiple InputSets depend on it. If recomputation fails, retain the previous committed result and report the failure.

Downstream invalidation follows input identity:

- Changed output content changes downstream provenance and requires new downstream results.
- Identical output content preserves downstream cache reuse.
- Identical bytes from a different producing subgraph remain distinguishable.

Consumers must bind to a consistent committed artifact version and digest during replacement. A force request is not permission to expose a partially replaced multi-output result.

### Core Provium support

Core now retains named bindings in [provenance records](../packages/provium/src/provium/provenance.py) and supplies matching pre-execution/reconstructed identities in [semantic.py](../packages/provium/src/provium/semantic.py). Physical UUIDs remain distinct from semantic identity. The core contract is:

| ID | Required capability |
| --- | --- |
| CORE-PROV-01 | Versioned deterministic provenance calculation before execution and reconstruction from finalized artifact lineage, with matching results and UUIDs excluded. |
| CORE-PROV-02 | Persist named invocation inputs, setup dependencies, and outputs. Preserve repeated uses of one artifact, supported collection order, and optional absence. Record declared bindings even if unread; runtime I/O tracking alone is insufficient. |
| CORE-PROV-03 | Canonical effective semantic configuration and input fingerprints according to this section, including configuration codec identity and procedure revision. |
| CORE-PROV-04 | Keep producing provenance separate from body content; propagate upstream provenance and content into downstream hashes. |
| CORE-PROV-05 | Verify an artifact against expected producing ancestry, output binding, type, and body digest without trusting its filename or an unchecked stored hash. |
| CORE-PROV-06 | Version the extended record/header representation. Keep legacy artifacts inspectable, but report insufficient legacy binding metadata as unverifiable instead of guessing or accepting a match. |

Keep these capabilities in core and reuse them from the pipeline. Procedures remain pipeline-independent. Verification establishes consistency of recorded lineage and content; it is not proof that a claimed program actually ran. Existing input artifacts form the pipeline boundary and must not require their producers to be rerun.

## 6. Artifact stores and publication

Support filesystem directories and S3 only. Use provenance-based addressing in both, independent of pipeline names and original filenames. CLI inspection provides human-readable discovery.

Resolve the store from `--store`, otherwise `PROVIUM_PIPELINE_STORE`. Accept relative/absolute local paths, `file://` locations, and `s3://bucket/optional-prefix`. Empty environment values are unspecified; reject unsupported schemes. The fallback is `./.provium/artifacts`; an empty environment value uses this fallback.

Use local scratch for downloads and staged outputs. `--scratch-directory` overrides the system temporary directory. Isolate execution/attempt files, retain them while needed, and clean up only owned temporary data. Scratch is not durable storage. S3 credentials come from runtime credential providers.

Publication must satisfy these guarantees:

1. Write and validate outputs in private temporary locations.
2. For local files, install completed artifacts atomically on the destination filesystem. If scratch is elsewhere, first copy into destination-side temporary files.
3. For S3, upload completed local artifacts; there is no rename/move step.
4. Accept the computation only when every required output is valid and publication is finished.
5. Ordinary cache fills reuse existing valid results. Explicit force replaces them under protected publication.
6. Reconcile completed publication after a lost acknowledgement without executing the procedure again.
7. Failed or stale attempts cannot expose partial output sets or overwrite a newer accepted result.

S3 checks query S3, not just local scratch. Use trustworthy metadata/checksums when sufficient; otherwise download and verify using core artifact validation. Do not assume an ETag is a universal content checksum. Distinguish missing objects from denied access, transport errors, and invalid content. Resolve uncertain upload outcomes by inspection.

Write immutable generations under `artifacts/<computation>/<generation>/<container-digest>.pa`. Direct execution atomically replaces `results/<computation>.json`; queued execution atomically replaces PostgreSQL’s authoritative result map under a live owner/generation/expiry check. The map includes every output location, UUID, and fingerprint. Force replaces this map, preserving files bound by existing readers. Stale uploads may leave unreferenced data but cannot replace a newer accepted map. Reconcile lost acknowledgements against the exact generation map. Garbage collection is deferred.

Direct execution initially assumes a single writer per store namespace. Concurrent writers sharing artifacts must use the same PostgreSQL coordinator and canonical store namespace. Dedicated S3 transfer queues/workers are not required.

## 7. Registry, queue, and leases

### PostgreSQL registry and queue

PostgreSQL is the only built-in persistent backend. Keep registry/queue interfaces backend-independent without implementing additional database adapters.

Resolve the connection from `--database`, otherwise `PROVIUM_DATABASE_URL`. Store credentials outside semantic graph configuration and provenance.

Registration persists objects by `(kind, name)`. Validate and write a batch atomically. Identical registration is idempotent; reject conflicting replacement until explicit registry revision/update behavior is designed. Registration does not enqueue inputs or execute procedures.

The InputSetQueue is a durable, deduplicating priority queue. Enqueue pins the pipeline snapshot and named existing artifact references. Enqueue requires no profile, worker settings, or destination store. Enforce uniqueness atomically on the pinned configuration plus canonical named artifact identities. Re-enqueue returns the existing record without resetting progress or failures.

Retain separate progress for each InputSet/pipeline even when computations are shared. Reconciliation must not create duplicate ready work. Ready-work discovery must be bounded and must not permanently overlook ready work behind blocked inputs.

### Claims and provenance leases

Use bounded read-only InputSet scans and atomic monotone progress updates. Computation lease acquisition is the ownership boundary; selection holds no InputSet lock, allowing other contexts to advance independent branches. Keyset pages default to 100 input sets; stable node priority applies across pages.

Execution ownership is keyed by **store namespace and computation provenance**, never by pipeline name or InputSet alone. This is what prevents two pipelines from intentionally executing the same shared step concurrently.

The coordinator must provide atomic acquisition, renewal, release, expiry, and stale-owner rejection. Use owner tokens and monotonically increasing generations with PostgreSQL time for lease expiry. Recheck the cache after acquisition to close races.

Do not hold database transactions open while computing or transferring files. Renew leases independently of procedure execution. Keep connection/transaction ownership explicit; do not pass live database connections into child processes.

On lost or uncertain ownership, stop admitting work and revoke publication authority. A disconnected worker may physically continue after expiry, but its stale outputs must not replace accepted results. Exactly-once physical execution under arbitrary crashes or network partitions is not promised.

Persist enough accepted publication metadata to reconcile all expected outputs after a crash. Recovery must distinguish finishing publication from executing a procedure again. Publication recovery must not bypass the no-retry-within-a-run rule.

Empty polls do not imply all queued InputSets completed. Distinguish idle work, work leased elsewhere, blocked dependencies, filtered/budget-deferred work, and failures. Notifications may optimize polling; durable progress cannot depend on one notification.

### Basic migrations

Provide numbered SQL migrations and an applied-version table. Persistent initialization acquires a migration lock, rechecks versions, and applies pending migrations in order. Each migration and its version record commit in one transaction.

Reject unknown/newer versions or gaps. Failed migrations roll back and stop initialization. Never reset data or silently downgrade. Direct execution does not initialize or migrate a database. A general migration framework, schema-diff engine, automatic down migrations, and rolling-upgrade guarantees are outside initial scope.

## 8. Execution and scheduling

The context accepts the definition/configuration, input source or queue, artifact store, procedure registry, and execution settings. It owns preparation, workers, cleanup, progress, and notifications.

### Procedure residency and priority

Only one configured node is resident within a context at a time. Worker counts allow separate process-local instances of that same node. Different contexts may work on different nodes concurrently.

Define left-most priority by stable topological order with node declaration order breaking ties. Select the earliest eligible node with ready uncached work. Reuse cached results without loading procedures.

Drain eligible ready inputs through the resident node before switching. At a switch, finish in-flight invocations and clean up all instances before loading the next node. Re-evaluate the live queue: newly submitted inputs or earlier-node work made ready externally may take priority then. Do not preempt active invocations.

Reuse prepared instances across compatible inputs. Instance compatibility includes procedure identity, semantic configuration, and setup dependencies. Instances are local to their worker process; one invocation runs at a time per instance.

Excluded nodes may supply existing cached outputs, but are never executed implicitly to satisfy dependencies. A run may produce final outputs, only intermediates, or nothing. Filtered work is partial, not failed.

### Thread and process behavior

Procedure execution always uses child processes, including the default single worker. The context may use internal threads for coordination, deadlines, lease renewal, and notifications, but never runs procedure setup or invocation on those threads. Worker results return internally to the context; workers do not dispatch public callbacks.

Initialize setup/model state independently in each worker and reuse prepared instances across compatible inputs. Coordination and heartbeat renewal must remain responsive while workers execute. The process boundary permits hard-limit termination while the context remains alive to reconcile publication and report outcomes.

### Time budgets and idle waiting

Profiles support independent soft and hard limits, all measured in seconds:

- `soft_time_limits` and `hard_time_limits`: ordered per-node rules using the same selectors and precedence as workers.
- `soft_time_limit` and `hard_time_limit`: global elapsed-time limits for this context invocation.

Resolve soft and hard rules independently. Omission or null means no corresponding limit. Both may be supplied, for example a soft stop at 300 seconds and hard stop at 360 seconds. Each is measured from the same timer origin; a hard limit is not an additional grace interval after the soft limit.

Node limits measure cumulative active wall-clock time using a monotonic clock. Count preparation, active execution/publication, and cleanup while the node occupies the execution slot; pause while another node runs or the context is idle. Count elapsed time once, not per worker, and retain accumulated time across switches. Cache-only reuse does not consume node budgets.

Global limits measure elapsed time from context execution start, before procedure preparation, including node switches and idle waiting. They apply to this invocation, not independently launched contexts. Limits never affect provenance.

At a **soft** limit, stop admitting new inputs to the affected node, or to all nodes for a global limit. Allow in-flight work to finish with lease renewal and protected publication. Check before each dispatch and while idle. A global soft limit also ends idle waiting.

At a **hard** limit, stop admissions and terminate affected in-flight procedure processes without waiting for invocation completion. Enforce this through independent deadline monitoring, not only the next dequeue. A node limit affects that node; a global limit affects all work in this context and ends idle waiting. Hard deadlines remain enforced during soft-limit draining.

Soft and hard limits are supported for every valid worker count, including the default of one. No special execution mode or extra worker is needed when a time limit is configured.

Fence interrupted attempts from new publication, discard unaccepted outputs, preserve previous committed results, and record time-limit interruptions without retrying them in the same run. Publication already accepted by the coordinator must be reconciled safely rather than arbitrarily killed midway through a commit. Hard limits terminate procedure execution, not the coordinator's integrity-preserving cleanup; cleanup may extend beyond the deadline. Successfully handled expiry still returns exit code 0.

When eligible ready work and local in-flight work drain, stop by default with current complete/partial outcomes. The `--idle-wait <seconds>` duration allows new eligible work to arrive. Hold no claims or transactions while waiting. Reset the idle deadline only after actual processing resumes. Work for excluded or exhausted nodes does not extend waiting or reset budgets.

Budgets do not persist across separate context invocations. Continuously arriving work may delay later nodes; fairness beyond the stated priority policy is not guaranteed.

## 9. Programmatic completion interface

Use context-owned callbacks:

- `on_step_finished`: reports an InputSet's configured step outcome, including fresh computation, cache reuse, or failure.
- `on_pipeline_finished`: reports that InputSet's complete, partial, failed, or cancelled outcome and available final outputs.

Both carry the original InputSet. Cache hits follow the same callback path as freshly computed results, including a fully cached pipeline. Include a reuse indicator and enough node/output/error information for callers.

Dispatch after accepting publication and progress. The context, not workers, invokes callbacks serially outside coordination/heartbeat work. Notify a logical step at most once per InputSet/node outcome within an invocation, then the pipeline outcome after applicable step notifications. Later invocations may notify again. No ordering across independent InputSets or global exactly-once delivery is promised.

Callback errors are logged and never cause procedure re-execution. Callbacks must not control dependency advancement. Buffer at most 1,024 notifications. Overflow/errors are logged. Shutdown allows up to two seconds to enqueue termination and two seconds to join the dispatcher. Callbacks must be short; delivery is best effort. Persistent status is authoritative after interruption.

No execution-finished callback, snapshot interface, result iterator, or public wait/polling API is required for the initial interface.

## 10. Failures, shutdown, and logs

Do not retry a failed computation during the same run. Track failures by computation identity across InputSets so node switches, reconciliation, duplicate notifications, or another dependent InputSet cannot trigger the same failure again.

Setup, execution, and storage failures must be logged and must not cause automatic procedure re-execution. Independent work may continue. Later explicit runs may retry failed work. `provium-pipeline retry <pipeline-name>` resets failed queued input sets to pending; enqueue never resets them.

Lease expiry is not permission to silently retry in the same run. First reconcile committed outputs. Keep failed attempts from exposing partial results, and preserve existing committed outputs when forced recomputation fails.

Cancellation and graceful shutdown stop new admissions and drain current work with lease renewal. A configured hard limit bounds non-cooperative procedure execution. Cancellation is context-wide and never invalidates shared committed artifacts. Forced termination relies on expiry and protected publication.

Log pipeline/node/procedure, InputSet and artifact identities, computation/attempt/run identities, phase, error, and traceback where available. Include setup, execution, storage, and callback errors. Exclude credentials and artifact payloads. Report where the run log is located.

Report complete/partial/failed/cancelled outcomes separately, available final outputs and cache hits. Logs include elapsed time, cumulative node active time, worker starts and lease contention. External side effects remain the procedure author's responsibility; artifact idempotency does not guarantee exactly-once API calls or database mutations.

## 11. CLI

### Run exit status

For both direct and queued `run`, exit code **0** means the execution process operated and shut down normally. Return 0 for an empty queue, no eligible work, cache-only completion, individual item/procedure failures (including all items failing), mixed outcomes, partial pipeline completion, and a normal stop at a node or global time limit. Per-item outcomes are reported through logs, callbacks, and status; they do not determine the process exit code.

Use a nonzero exit code when the command itself cannot initialize or continue operating, such as invalid configuration/arguments, an unrecoverable coordinator failure, or an unhandled runtime error. A handled item-level error is not a process failure. Do not mask process crashes or externally forced termination as successful execution. This policy applies to `run`; it does not redefine error reporting for registration, validation, or other commands.

Start documentation with direct execution, followed by persistent usage. Preserve these command interfaces:

| Operation | Interface |
| --- | --- |
| Direct execution | `provium-pipeline run -f pipeline.yaml [-f profiles.yaml] --input name=artifact-reference ...` |
| Register objects | `provium-pipeline register -f pipeline.yaml [-f profiles.yaml]` |
| Enqueue one InputSet | `provium-pipeline enqueue <pipeline-name> --input name=artifact-reference ...` |
| Process queued inputs | `provium-pipeline run <pipeline-name>` |
| Inspect direct inputs | `provium-pipeline status -f pipeline.yaml --input name=artifact-reference ...` |
| Inspect registered pipeline | `provium-pipeline status <pipeline-name> [--input name=artifact-reference ...]` |

Direct execution accepts one PipelineConfiguration and requires no persistent queue. Registration may accept multiple pipeline/profile documents. Registered execution and direct file-based execution are mutually exclusive selection modes. Multiple `--input` arguments form one InputSet; repeat enqueue for additional sets. A programmatic run can supply multiple InputSets; bulk CLI input syntax is deferred.

Both execution modes support store/scratch options, profile selection, node/tag filters, worker overrides, and force selectors. Persistent commands use the database option/environment variable. Enqueue does not select compute settings.

Status must not load procedure implementations or execute missing work. `--outputs` lists available intermediate/final artifacts by logical node/output name, provenance, and actual local/S3 location. `--only` filters inspection; `--json` supports scripts. Supplying inputs to registered status inspects without enqueueing. Report missing/corrupt artifacts honestly; show expected locations only when provenance can be determined.

Provide validation and resolved-settings inspection so users can see graph errors, eligible nodes, effective workers, and time limits before execution. Additional command spelling is an implementation detail.

Slurm and AWS Batch launch the same queued execution interface in externally configured environments. Compatible code, PostgreSQL access, and accessible input/store locations are required. Hardware requests and scheduler submission details remain deployment concerns.

## 12. Acceptance criteria

These scenarios define regression coverage for the implementation.

| Area | Required scenarios |
| --- | --- |
| Graph validation | Valid alternate graphs satisfy one definition; cycles, incompatible/missing bindings, duplicate YAML keys, and unsupported fields fail before expensive setup. |
| Profiles | Specificity and ordered overrides work; defaults are one worker/unlimited time; zero, negative, non-integer, and boolean worker counts are rejected; invalid profiles/selectors fail clearly. |
| Stable provenance | Fresh execution/output UUIDs do not change semantic hashes; renaming pipeline/nodes and changing compute settings have no effect. |
| Binding fidelity | Repeated artifact bindings, swapped named inputs, setup changes, collection order, absent inputs, and multiple output names remain distinguishable as required. |
| Core verification | Pre-execution and reconstructed hashes agree after serialization/reload; wrong ancestry, output bindings, corrupt bodies, and insufficient legacy metadata are rejected. |
| Reuse and force | Equivalent pipelines share results; force replaces without comparison; unchanged bytes preserve downstream reuse, changed bytes invalidate it, and failed force retains the prior result. |
| Publication | Multi-output failures never expose a completed set; stale owners cannot publish over accepted results; uncertain upload/acknowledgement outcomes reconcile without re-execution. |
| Queue | Concurrent duplicate enqueue creates one record; re-enqueue does not reset progress; independent branches can progress in separate contexts. |
| Cross-pipeline locking | Equivalent computations compete for one provenance lease; lease loss and publication recovery are tested with interleavings and process failures. |
| Scheduling | One node remains resident per context; inputs drain through it; external earlier-node work is reconsidered at the next switch. |
| Time limits | Active time survives switches; soft expiry drains in-flight work; hard expiry independently terminates process execution, including during soft draining; the context survives worker termination and reports interruptions. |
| Run exit status | Empty, failed-item, mixed, partial, and time-limited runs exit 0 when the executor operates normally; command/runtime failures remain nonzero. |
| Global time limit | Elapsed time includes idle periods and switches; expiry stops all new admissions and waiting; soft expiry drains, hard expiry terminates affected process execution safely. |
| Partial runs | Filters, exhausted budgets, empty input sources, and direct-input final outputs produce accurate outcomes without unnecessary procedure loading. |
| Process isolation | Default execution uses one child process; procedure preparation/invocation/cleanup stay out of the coordinating process; prepared instances are reused across compatible inputs. |
| Notifications | Fresh and cached results both notify; duplicate worker messages do not duplicate logical callbacks; callback errors do not affect computation. |
| Failure policy | Equivalent failed work is not retried within a run; independent work continues; shutdown and restart preserve committed results. |
| Storage | Local paths, file URLs, and S3 work with scratch isolation; access errors are not cache misses; S3 verification downloads when needed. |
| Migrations | Concurrent initialization is serialized; migration/version writes are atomic; incompatible schemas are rejected without data loss. |

Implement in small test-first increments with expected-failure verification, minimal code, review, and relevant lint/type checks. Distributed correctness and forced replacement require concurrency/failure tests before being advertised.

## 13. Resolved implementation details

- Reuse core canonical JSON/SHA-256. Computation/output hash envelopes carry version 1; named execution records carry `bindings_version: 1`. Legacy records remain readable but unverifiable for reuse.
- Leases use PostgreSQL time, a 60-second duration, five-second renewal, owner UUIDs and increasing generations.
- The public API includes `PipelineDefinition`, `PipelineConfiguration`, `ComputeProfile`, `InputSet`, `ArtifactStore`, `ExecutionContext`, `Outcome`, and `PostgresBackend`. `start` returns immediately; context-manager exit joins execution. A context starts once.
- Definitions use `--definition module:attribute` or a `provium.pipeline.definitions` entry point named after the configuration. Supplied definitions are persisted and pinned. Without one, graph compatibility is inferred from standard procedure contracts.
- Default logs are `<system-temp>/provium-<run-id>.log`; Python callers may set `log_path`.
- Stores own private download directories. CLI shutdown closes its store; Python callers use the store context manager or `close()`. Remote `Artifact.path` lasts until store close; `Artifact.location` is durable.
- Direct execution uses a host-local advisory writer lock on POSIX systems. Multiple hosts must use PostgreSQL. Do not mix coordinated and uncoordinated writers in one namespace.
- `validate` and `inspect` report graph order and resolved settings. Status is JSON. Queued status uses recorded store locations even when the default changes.
- Registry replacement/revisions, bulk CLI input syntax, artifact garbage collection and scheduler submission adapters remain outside initial scope.
