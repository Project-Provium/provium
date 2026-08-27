# Operations and troubleshooting

This guide covers the reference local runtime. Provider distributions may expose additional operational controls while preserving the same run/task/dispatch semantics.

## Before running

Verify:

- Python is 3.12 or newer;
- installed `provium` satisfies the declared version range and core API version;
- artifact/procedure/pipeline/resolver entry points are installed in the same environment;
- the database parent is writable;
- the artifact/workspace filesystem has sufficient space;
- every frozen input artifact has at least one active readable location.

Use JSON output in automation and preserve complete error envelopes.

## Choosing a database location

Set an explicit path for non-demo use:

```bash
export PROVIUM_PIPELINE_DATABASE=/absolute/workspace/.provium/pipeline.sqlite3
```

The parent directory becomes the local data root. Avoid placing an actively written SQLite database on a filesystem with unreliable locking. Ensure worker processes use the same canonical path and artifact root.

## Inspecting progress

```bash
provium run status RUN_ID --format json
provium run tasks RUN_ID --format json
provium dispatch show DISPATCH_ID --format json
provium dispatch wait DISPATCH_ID --format json
```

Interpret the levels separately:

- run state summarizes the overall workload;
- dispatch state summarizes one execution request;
- task state identifies the exact blocked/ready/leased/retry/terminal work;
- attempt history identifies ownership and retries.

A failed dispatch does not erase succeeded tasks. A later dispatch can target failed or remaining work.

## Resuming work

After interruption:

1. verify the prior process is no longer active;
2. inspect task and dispatch state;
3. allow/recover expired leases as appropriate to the runtime;
4. execute `--all-remaining` for incomplete work or `--only-failed` for terminal failures;
5. include missing upstream only when the selected downstream task needs it.

```bash
provium run execute RUN_ID --all-remaining --format json
```

Do not delete the database to “retry.” That discards the durable information needed to resume safely.

## Retrying a dispatch

```bash
provium dispatch retry DISPATCH_ID --format json
```

This preserves the original selection boundary and dependency policy while selecting failed tasks. Use run-level selection when you intentionally want a different boundary.

## Cancellation

```bash
provium dispatch cancel DISPATCH_ID --format json
provium run cancel RUN_ID --format json
```

Cancellation stops future work and signals active work when the runtime has an active cancellation bridge. Already accepted task outputs remain valid. Inspect terminal states before repeating commands; cancellation is not a data-deletion operation.

## Common diagnostics

### Incompatible core API

Install the compatible Provium range declared in `pyproject.toml`. Do not bypass `require_compatible_core()`; the package relies on public core execution/container contracts.

### Unknown pipeline or duplicate registration

Use `provium pipeline list --format json`. Confirm the distribution’s entry point, catalog target, definition identifier/version, and that only one installed source owns it.

### Compilation error

Read every structured diagnostic. Fix the definition, catalog contract, binding cardinality, cycle, output reference, or configuration value. Existing runs remain bound to their compiled snapshots.

### Resolver options/configuration error

`pipeline enqueue` and `pipeline execute` require both `--records-from` and `--config`. The configuration must decode to a mapping and match the resolver’s expected schema.

### Unsatisfied selected dependency

Either select the prerequisite explicitly, use `--include-missing-upstream`, or execute prerequisite work first. Do not use upstream expansion blindly if the narrow boundary was intentional.

### Unknown or invalid identifier

Run, dispatch, task, and attempt identifiers are UUID-backed. Copy the ID from structured command output and preserve it as a string.

### Artifact has no active location

The logical identity exists but cannot be materialized. Restore/register a valid copy, reactivate a verified location, or repair provider availability. Recreating the run does not fix missing artifact bytes.

### Corrupt artifact

Quarantine/deactivate the bad location and restore another verified copy. If no valid copy exists, reproduce the upstream computation from retained inputs.

### Task output conflict

Two attempts tried to record different complete results for one task. Preserve the database and investigate lease fencing, provider idempotency, procedure determinism, and artifact identity behavior. Do not overwrite the accepted set manually.

### State transition conflict

Another actor changed the state after the caller read it. Reread current state. Terminal cancellation/success may be a valid winner; repeated nonterminal conflicts suggest competing coordinators.

### Database locked/busy

Check for long transactions, duplicate coordinators, unsuitable network filesystems, and process crashes. SQLite is intended for coordinated processes on one host. Do not increase timeouts indefinitely to hide an invalid deployment topology.

## Disk management

Separate categories when investigating disk usage:

- managed artifact objects: durable, indexed content;
- staging files: publication in progress or crash residue;
- materialized inputs: attempt-owned copies;
- output workspaces: core procedure destinations before import;
- SQLite database/WAL files: durable metadata.

Never recursively delete the data root while runs exist. Use retention and garbage-collection services for managed objects and a workspace cleanup policy that checks active leases/attempts.

## Garbage collection operations

A safe operator workflow is:

1. query old unreferenced physical locations;
2. create/store a deterministic GC plan with a grace cutoff;
3. review candidate count/size and retention sources;
4. apply the exact plan;
5. inspect deleted versus skipped results;
6. deactivate/remove corresponding location metadata according to adapter policy;
7. keep an audit record.

Because apply revalidates retention, skipped candidates are expected under concurrency.

The current package supplies the plan/apply service; scheduling, durable plan persistence, and administrative CLI orchestration are deployment/provider responsibilities.

## Backups and restore checks

Back up metadata and artifact objects as one logical unit. Periodically test restoration by:

- opening the copied database;
- querying runs/tasks/input sets;
- resolving active artifact locations;
- materializing and verifying sample artifacts;
- exporting a complete run bundle;
- resuming a non-production test run.

A backup is not proven by successful file copy alone.

## Observability

Useful structured events/metrics for a composed runtime include:

- run/dispatch/task state transitions and conflicts;
- claim latency, lease renewals, expiries, and recoveries;
- attempt duration and retry decision;
- materialization/publication bytes and duration;
- cache hit/reservation/wait/stale rates;
- active-location count and verification age;
- orphan/GC candidate/deleted/skipped bytes;
- worker child exit/replacement and coordinated shutdown duration.

Use IDs as correlation fields. Avoid logging resolver configuration or artifact locator credentials wholesale.

## Security considerations

- Treat pipeline/config/resolver files as untrusted structured input.
- Concrete Python targets and installed entry points are executable code; install only trusted distributions.
- Keep credentials outside canonical snapshots, locators, labels, and exports.
- Restrict database/artifact-root permissions.
- Validate adapter locators to prevent path traversal or cross-store confusion.
- Do not expose `--locations` output to users who should see semantic results but not storage topology.

## Current limitations before Phase 14

The package is pre-alpha. The default CLI uses serial execution, local SQLite, and the filesystem store. The library has multiprocess supervision primitives but no provider-neutral CLI worker-count option. GC scheduling and durable administrative plans are not exposed as CLI commands. Release hardening still needs package-wide reproducibility, public API review, executable documentation examples, and performance baselines.
