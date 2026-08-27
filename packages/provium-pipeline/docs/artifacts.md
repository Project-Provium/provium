# Artifacts

Provium core defines and finalizes artifact containers. Provium Pipeline manages finalized artifacts across runs, tasks, caches, and physical stores.

## Ownership boundary

Provium core owns:

- artifact definitions and concrete reader/writer classes;
- typed read/write bindings;
- body staging and header finalization;
- artifact identity, body/container digests, lineage, and verification;
- transactional procedure output files.

Provium Pipeline owns:

- managed descriptors extracted through public core inspection APIs;
- physical store locations;
- publication, materialization, and deletion protocols;
- logical indexing and relationships;
- input/run/task/cache bindings;
- retention references and garbage collection.

The pipeline package never parses private core container internals.

## Managed descriptor

`ManagedArtifactDescriptor` is immutable metadata for one logical artifact:

- identity;
- artifact definition identifier;
- body digest;
- optional container digest;
- size in bytes;
- creation timestamp.

Registering the same identity is idempotent only when immutable fields match. A mismatch is an identity collision and must not overwrite existing metadata.

## Location

`ArtifactLocation` describes one copy:

- store identifier;
- adapter-specific canonical locator;
- active/inactive state;
- optional size;
- created and verified timestamps.

A descriptor can have multiple locations. Queries and materialization use active locations. Deactivation preserves audit without pretending a missing copy is usable.

## Store protocol

`ArtifactStore` defines the physical operations:

- `publish(source, descriptor)`;
- `stat(location)`;
- `materialize(descriptor, locations, destination)`;
- `delete(location)`;
- `list_orphans(query)`.

Required behavior includes idempotent publication/deletion, no mutation of a different object at an existing locator, immediate readability after publish, complete verified materialization, and categorized errors for conflict, missing data, corruption, permission, transient service, and invalid locators.

## Filesystem store

`FilesystemArtifactStore` is the local reference adapter. Its deterministic layout is:

```text
ROOT/
  objects/
    <first-two-identity-chars>/
      <artifact-identity>.pa
  staging/
  materialized/
```

Publication copies to a staging file, verifies source stability, fsyncs as required, and atomically installs the identity-derived destination. Publishing the same verified bytes returns the existing location. Different bytes at the same logical destination produce a conflict.

Materialization prefers safe local filesystem operations, verifies the result, and returns cleanup ownership. Orphan listing reports old physical objects for lifecycle evaluation without deleting them.

## Index protocol and SQLite adapter

`ArtifactIndex` separates metadata relationships from physical storage. It supports:

- registering and resolving descriptors;
- registering/deactivating locations;
- listing active locations;
- binding artifacts to runs, tasks, records, nodes, output fields, input sets, and cache entries;
- retention lookup and lifecycle queries.

`SQLiteArtifactIndex` persists descriptors and locations in the local metadata database. It validates immutable identity fields, location identity, and active-location queries transactionally.

## Import service

`ArtifactImportService.import_artifact()` moves a finalized local artifact into managed ownership:

1. inspect and fully verify the `.pa` file through core APIs;
2. extract a `ManagedArtifactDescriptor`;
3. publish it to the configured store;
4. register descriptor and location in the index;
5. return the managed descriptor/identity.

Run snapshots and task output sets retain only logical identities. The original path can disappear without invalidating durable state.

## Materialization

A task needs local input paths even when managed storage is remote or content-addressed. `materialize_binding_inputs()` receives compiled binding cardinality, ordered identities, descriptors/locations, an artifact store, a workspace, and `AttemptMaterializations` ownership.

For each identity it:

- chooses available active locations through the store;
- creates a deterministic attempt-local path;
- materializes and verifies a complete artifact;
- tracks cleanup ownership;
- preserves binding order.

The resulting paths are wrapped in core typed read bindings. Cleanup occurs even when invocation construction or execution fails.

## Output publication

Core produces finalized output files in the attempt workspace. `LocalTaskAttemptExecutor` imports each produced output and records the returned identity. Optional outputs that core reports absent are recorded as `None` and are not imported.

The output store records the complete field set together. This prevents downstream consumers from seeing a partially accepted multi-output task.

## Artifact queries

`ArtifactQueryService` builds immutable query results from binding providers and location lookup.

Run queries can filter by:

- run ID;
- record key;
- node ID;
- output field;
- whether locations should be included.

Input-set queries list artifact membership. Results preserve binding metadata and optionally attach current active locations. Location inclusion is opt-in because physical placement can be large or sensitive and is not needed for most semantic queries.

The CLI exposes these as:

```bash
provium run artifacts RUN_ID --locations --format json
provium input-set artifacts INPUT_SET_ID --locations --format json
```

## Retention references

`RetentionReference` records an artifact identity, reference kind, and owner identity. `InMemoryRetentionIndex` is the deterministic local/reference implementation.

Typical retention owners include:

- frozen run inputs;
- named input sets;
- accepted task/run outputs;
- valid computation-cache entries;
- explicit administrative holds.

Retention applies to logical artifacts. A provider adapter may need to ensure at least one healthy location remains for each retained identity.

## Garbage collection

`GarbageCollectionObject` represents a physical location with the time it became unreferenced. `GarbageCollectionService` uses plan/apply:

```text
old unreferenced locations
          |
          +-- retained identity? --> exclude
          v
frozen deterministic plan
          |
          +-- retained since planning? --> skip
          v
idempotent physical deletion
```

`plan()` rejects negative grace periods, filters by cutoff and retention, and sorts candidates by artifact/location identity. `apply()` revalidates each identity, deletes eligible locations, records deleted/skipped objects, and memoizes the result by plan ID. Reusing a plan ID with different content is an error.

The service accepts a deletion callable, so it is store-agnostic. Candidate discovery and durable plan storage can be supplied by a provider adapter.

## Orphans versus unretained artifacts

An orphan is a physical object not represented by an accepted logical relationship, often due to a crash after publication. An unretained artifact may still be correctly indexed but no longer needed.

Both require a grace period and revalidation. Immediate deletion based on one query would be unsafe around concurrent run creation, output acceptance, or cache publication.

## Conformance tests

The artifact package includes reusable store/index conformance helpers. Every adapter should prove:

- publication and materialization round trips;
- idempotency and conflict behavior;
- deletion semantics;
- corruption detection;
- locator validation;
- active-location behavior;
- descriptor identity collision handling;
- real storage interactions rather than mock-only behavior.

The filesystem and SQLite references run against real files and databases.
