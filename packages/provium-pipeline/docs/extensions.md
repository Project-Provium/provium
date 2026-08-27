# Extension and plugin guide

`provium-pipeline` is local-first but adapter-oriented. Extensions should implement narrow public contracts and register them through documented entry points rather than patching the environment backend.

## Compatibility boundary

Importing `provium_pipeline` calls `require_compatible_core()`. The distribution supports Provium core API version 1 and currently declares `provium>=0.7,<0.8`.

Extensions should depend on public exports from `provium` and `provium_pipeline`. Do not import private core container/header modules or copy internal SQLite schemas.

## Pipeline catalogs

Register installed definitions with the `provium.pipeline_catalogs` entry-point group.

```toml
[project.entry-points."provium.pipeline_catalogs"]
example = "example_pipelines.catalog:catalog"
```

The target must load a `PipelineCatalog`. A catalog can register in-memory definitions, lazy Python targets, or package resources. Use globally unique pipeline identifiers and stable versions.

Discovery reports entry-point name, distribution/version, definition identifier, target/resource, duplicate source, and exception chain.

### Catalog design guidance

- Keep catalog import lightweight.
- Put execution-only imports behind lazy concrete procedure targets.
- Package YAML/JSON resources with the distribution.
- Treat an existing identifier/version as immutable semantic content.
- Test discovery from an installed wheel, not only a source checkout.

## Input record resolvers

Register resolvers with `provium.input_record_resolvers`.

A resolver implements the async `InputRecordResolver` contract and receives `ResolveInputRecordsRequest` plus `InputResolutionContext`.

Resolver responsibilities:

- validate its canonical configuration;
- query the external source once;
- return unique stable record keys;
- use managed artifact identities, importing through provided services where appropriate;
- preserve repeated binding order;
- return source metadata sufficient for audit;
- avoid secrets in durable metadata.

Resolvers should not return open sessions, paths that will disappear, or lazy iterators tied to external state. The result is frozen immediately.

Test duplicate keys, empty results, malformed configuration, provider failure, deterministic output, and wheel entry-point discovery.

## Artifact stores

Register physical stores with `provium.artifact_stores`.

```toml
[project.entry-points."provium.artifact_stores"]
my-store = "my_package.store:artifact_store_factory"
```

Implement `ArtifactStore` semantics, including publication conflict behavior, complete verified materialization, idempotent deletion, locator validation, and orphan listing.

Locator JSON is adapter-owned but must be canonical, serializable, and free of transient credentials. Store credentials belong in runtime configuration/context.

Run the reusable artifact-store conformance suite against the real provider service or a faithful containerized implementation.

## Artifact indexes

Register indexes with `provium.artifact_indexes`.

```toml
[project.entry-points."provium.artifact_indexes"]
my-index = "my_package.index:artifact_index_factory"
```

An index must preserve immutable descriptor identity, location history/state, active-location lookup, and relationship queries. Claims/retention behavior must be transactional enough for the provider’s concurrency model.

Do not combine logical identity with one physical URI. Multiple locations and location deactivation are core assumptions of the pipeline domain.

## Execution stores

`ExecutionStore`, `DispatchStore`, attempt lease, input-set, and task-output protocols can be implemented for another database.

Provider adapters must preserve:

- atomic run plus task creation;
- idempotency-key conflict detection;
- expected-state compare-and-set transitions;
- canonical versioned payload round trips;
- atomic leases with fencing tokens and expiry;
- complete output-set acceptance;
- stable ordering required by queries/exports.

A store that merely provides CRUD but loses compare-and-set or lease token semantics is not conformant.

## Worker runtimes

A worker runtime composes the existing semantic pieces:

- dispatch membership and state;
- execution/task store;
- attempt leases;
- `FrozenTaskInvocationBuilder` or an equivalent contract-preserving builder;
- `LocalTaskAttemptExecutor`-equivalent acceptance;
- retry/cancellation policy;
- a process/thread/task supervisor.

Remote workers must reconstruct execution from durable snapshots and verify installed procedure/configuration contracts. Do not send mutable definition paths as the work payload.

## Multiprocess local runtime

Use `MultiprocessSupervisor` with a process factory. Each child should:

1. open its own database/store clients;
2. build its own procedure executor and prepared cache;
3. use a stable slot-derived worker identity plus per-attempt token;
4. run the shared dispatch worker loop;
5. close process-local caches and clients on exit.

The parent supplies terminal-state polling and coordinated cancellation/shutdown hooks.

## CLI extensions

This distribution already registers its command catalog through the core `provium.cli.command_catalogs` group. Applications should add separate core CLI plugins rather than replacing `provium` or monkey-patching `LocalCLIBackend`.

Use versioned JSON envelopes, nonzero error exits, and structured diagnostics consistent with the core CLI contract.

## Public definition/procedure packages

A package providing actual pipeline content normally publishes:

- artifact definitions and lazy concrete artifact targets;
- procedure definitions/contracts and lazy concrete procedure targets;
- pipeline catalog definitions/resources;
- optionally input record resolvers.

Compilation environments need the lightweight definitions/contracts. Execution environments additionally need concrete targets and their dependencies.

## Testing an extension

Minimum useful matrix:

- unit tests for validation and error classification;
- public protocol/conformance tests;
- real database/filesystem/provider integration;
- multiprocess contention where claims are involved;
- built-wheel entry-point discovery;
- clean-container CLI flow;
- failure injection around publication/commit/retry boundaries;
- Pyright against the public typed contracts.

Mocks are appropriate for narrow collaborator behavior, not for replacing the provider behavior the adapter claims to implement.

## Versioning advice

Version stored payloads and external documents independently from the package version. Reject unknown schemas explicitly. Keep identifiers and immutable digests stable. Use additive/expand migrations before removing fields or changing semantics.

If an extension changes the computation-relevant meaning of a contract, change the contract/version digest rather than silently reusing old cache identities.
