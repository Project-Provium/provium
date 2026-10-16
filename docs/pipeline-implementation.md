# Pipeline implementation and verification

The package implements the consolidated [requirements](pipeline-requirements.md).
See [usage examples](pipeline-api-examples.md) for direct CLI execution first,
then profiles, callbacks, PostgreSQL queues, and deployment.

## Delivered

- Validated graph/definition models, standard procedure contracts, multi-document
  YAML, node tags, filters, and independent compute profiles.
- Core named provenance bindings and deterministic semantic identities, with
  separate body digests and legacy-artifact compatibility checks.
- Filesystem/S3 stores, verified cache reuse, forced replacement, immutable artifact
  generations, and atomic multi-output result acceptance.
- PostgreSQL registration, pinned input sets, deduplication, bounded priority
  scanning, renewable fenced computation leases, and numbered migrations.
- Asynchronous coordination, reusable spawned process workers, soft/hard time
  limits, serial completion callbacks, cancellation, logs, and read-only status.
- Standalone and core-plugin CLIs, explicit retry, wheel/source packaging.

## Test-first implementation and six review passes

Each behavioral fix was preceded by a failing regression test, then retested.

1. **Core provenance and validation:** checked UUID independence, canonical
   configuration, named input/setup/output bindings, repeated bindings, absence,
   legacy records, and malformed metadata.
2. **Storage and distributed ownership:** checked stale/expired leases, force
   preservation, atomic result visibility, input identity pinning, and cross-pipeline
   computation sharing.
3. **Scheduling and lifecycle:** fixed duplicate dispatch, bounded queue paging,
   original InputSet callback identity, status accidentally retaining work, store
   download cleanup, and closed-context handling.
4. **Recovery and failure injection:** fixed lost PostgreSQL acknowledgements,
   verified lost S3 acknowledgements, checked failed-work suppression and independent
   inputs, and fixed status after store changes and cancelled-work resumption.
5. **Final API/code review:** fixed worker-exit dispatch races, definition snapshot
   mutation, missing initial inputs, and non-finite idle waits. Rechecked branching,
   joins, setup dependencies, multi-output publication, descendant reuse after force,
   packaging, formatting, and strict typing.

6. **CI and coverage review:** retained the existing 100% gate, added validation,
   protocol, shutdown, optional/repeated-binding and failure-path tests, removed
   redundant dispatch bookkeeping, and supplied PostgreSQL/LocalStack plus example
   catalogs in CI and release jobs.

These were self-review passes with regression tests, not independent human reviews.
No known blocking issue remains within the documented initial scope.

## Verification

- Core: **704 tests**, **100% statement and branch coverage**.
- Pipeline: **136 tests**, **100% statement and branch coverage**, including real PostgreSQL 17 and LocalStack S3.
- Example package: **2 tests**.
- Release configuration: **5 tests and 2 subtests**.
- Ruff lint and formatting checks pass.
- Strict Pyright passes for both core and pipeline.
- Wheel and source distributions build successfully.

The pipeline suite includes concurrent queue submission, concurrent cross-pipeline
execution, stale generation rejection, migration serialization/rollback, worker
process isolation, hard termination, soft draining, callback errors, cache reuse,
and forced replacement. Integration setup commands are in the package README.

## Operational boundaries

Direct mode uses a POSIX host-local single-writer lock. Cross-host writers need
the same PostgreSQL coordinator and canonical store namespace. Do not mix direct
and coordinated writers concurrently in that namespace.

Slurm and AWS Batch use the same CLI with externally allocated hardware and
installed procedure catalogs. Actual Slurm/AWS Batch jobs and production AWS S3
were not exercised; S3 integration used LocalStack.

Callbacks are bounded, best-effort notifications. PostgreSQL progress and accepted
result references are authoritative. Arbitrary procedure side effects are outside
artifact idempotency; physical exactly-once execution across partitions is not
promised. Obsolete/unreferenced artifact generations require external cleanup until
garbage collection is added. Legacy artifacts without named bindings must be
regenerated before verified pipeline reuse.
