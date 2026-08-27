# provium-pipeline

Typed, reproducible, idempotent local pipelines built on the public Provium core contracts.

The package requires Python 3.12 or newer and Provium core API version 1. Installing it adds pipeline commands to the existing `provium` executable through the public CLI plugin entry point.

## Documentation

Start with the [documentation guide](docs/index.md), which provides reading paths for users, operators, and contributors. For a progressive, executable introduction, use the [Jupyter tutorial course](notebooks/README.md).

- [Core concepts and vocabulary](docs/concepts.md)
- [Architecture and end-to-end data flow](docs/architecture.md)
- [Pipeline definitions and compilation](docs/definitions-and-compilation.md)
- [Inputs, input sets, and runs](docs/inputs-and-runs.md)
- [Dispatch and execution](docs/execution.md)
- [Artifacts, publication, retention, and garbage collection](docs/artifacts.md)
- [Storage, transactions, and recovery](docs/storage-and-recovery.md)
- [CLI reference and workflows](docs/cli.md)
- [Extension and plugin guide](docs/extensions.md)
- [Operations and troubleshooting](docs/operations.md)
- [Testing architecture](docs/testing.md)
- [Python module responsibility map](docs/api-reference.md)

## Local storage

The local backend stores durable state in `.provium/pipeline.sqlite3` and managed artifacts under `.provium/artifacts`. Set `PROVIUM_PIPELINE_DATABASE` to place the database and its sibling artifact/workspace directories elsewhere.

## Command groups

Use `provium GROUP --help` for the complete options and selection flags.

- `provium pipeline`: validate, show, list, enqueue, or immediately execute pipeline definitions.
- `provium input-set`: create and inspect immutable named input cohorts.
- `provium run`: create, execute, query, cancel, and export reproducible runs.
- `provium dispatch`: inspect, wait for, cancel, or retry an execution dispatch.

Every command accepts the core CLI output options. Use `--format json` for the versioned `provium.pipeline-cli/v1` envelope; invalid arguments and runtime diagnostics return a nonzero exit code in the same structured form.

## End-to-end example

Validate a JSON or YAML pipeline definition, create a frozen input set from NDJSON records, then create and execute a run:

```bash
provium pipeline validate pipeline.yaml
provium input-set create inputs.ndjson --identifier evaluation-1
provium run create pipeline.yaml --input-set evaluation-1 --format json
provium run execute RUN_ID --all --format json
provium run status RUN_ID --format json
provium run outputs RUN_ID --format json
provium run artifacts RUN_ID --locations --format json
```

A record resolver can create a run without first naming an input set:

```bash
provium pipeline enqueue pipeline.yaml \
  --records-from example.ready-records \
  --config resolver.yaml \
  --all --format json
```

Use `pipeline execute` with the same options to create the run and process its dispatch immediately.

## Selection and resumption

Run and pipeline execution support selection by node, procedure, record, and label, plus `--all`, `--all-remaining`, and `--only-failed`. `--include-missing-upstream` expands selected work to unfinished dependencies; the default selected-only policy reports an actionable dependency diagnostic instead. Re-running an interrupted run processes incomplete task boundaries while preserving committed results.

## Queries and exports

```bash
provium input-set artifacts INPUT_SET_ID --locations --format json
provium run tasks RUN_ID --format json
provium run inputs RUN_ID --format json
provium run configuration export RUN_ID --output resolved.json
provium run export RUN_ID --output run-bundle.json
provium dispatch wait DISPATCH_ID --format json
provium dispatch retry DISPATCH_ID --format json
```

Run artifacts can include active physical locations with `--locations`. Exported configuration and run bundles are generated from frozen durable snapshots rather than rereading mutable source files.
