# CLI reference and workflows

Installing `provium-pipeline` registers command catalogs with the existing `provium` executable through `provium.cli.command_catalogs`. Use `provium GROUP --help` and `provium GROUP ACTION --help` for exact parser options.

## Output contract

Commands support human output and versioned JSON.

```bash
provium run status RUN_ID --format json
```

JSON uses the envelope:

```json
{
  "schema": "provium.pipeline-cli/v1",
  "data": {}
}
```

Errors use the same envelope with an error type and message and return a nonzero exit code. Invalid arguments, invalid identifiers, missing resources, compilation diagnostics, and storage failures are not printed as successful data.

## Local environment

The environment backend reads:

```text
PROVIUM_PIPELINE_DATABASE
```

Default: `.provium/pipeline.sqlite3`. The database parent also contains managed artifacts and workspaces.

The installed environment discovers core artifact/procedure catalogs, pipeline catalogs, and input resolver entry points. It composes SQLite metadata stores, the filesystem artifact store, a core `ProcedureExecutor`, and a serial dispatch worker.

## Pipeline commands

### Validate

```bash
provium pipeline validate pipeline.yaml
```

Loads YAML/JSON or a discovered catalog definition, compiles against installed lightweight contracts, and returns structured diagnostics. It does not execute concrete procedures.

### Show

```bash
provium pipeline show pipeline.yaml --format json
```

Prints the canonical definition document.

### List

```bash
provium pipeline list --format json
```

Lists discovered installed pipelines and their registration provenance.

### Enqueue from a resolver

```bash
provium pipeline enqueue pipeline.yaml \
  --records-from example.ready-records \
  --config resolver.yaml \
  --all \
  --format json
```

Resolves/finalizes inputs and creates a planned run without executing it. Both resolver options are required together.

### Execute from a resolver

```bash
provium pipeline execute pipeline.yaml \
  --records-from example.ready-records \
  --config resolver.yaml \
  --all \
  --format json
```

Creates the same reproducible run and immediately executes the selected dispatch.

## Input-set commands

### Create

```bash
provium input-set create inputs.ndjson \
  --identifier evaluation-2026-08 \
  --label purpose=evaluation \
  --format json
```

Omit the source path to read NDJSON from standard input. The identifier names immutable content.

### List and show

```bash
provium input-set list --format json
provium input-set show evaluation-2026-08 --format json
```

### Export records

```bash
provium input-set export evaluation-2026-08 --output inputs.ndjson
```

Writes deterministic frozen NDJSON.

### Query artifacts

```bash
provium input-set artifacts evaluation-2026-08 --locations --format json
```

`--locations` includes current active physical placements.

## Run commands

### Create

```bash
provium run create pipeline.yaml \
  --input-set evaluation-2026-08 \
  --all \
  --format json
```

The command returns the run ID and planned status. A direct record selector may be supplied according to the selection flags, but record artifact content must already be represented in the chosen input source.

### Execute

```bash
provium run execute RUN_ID --all --format json
```

Other useful forms:

```bash
provium run execute RUN_ID --all-remaining
provium run execute RUN_ID --only-failed
provium run execute RUN_ID --node tokenize --record record-001
provium run execute RUN_ID --label split=evaluation
provium run execute RUN_ID --node downstream --include-missing-upstream
```

Selections can filter nodes, procedures, records, and labels. Without `--include-missing-upstream`, unfinished unselected dependencies produce an actionable diagnostic.

### Status, tasks, inputs, and outputs

```bash
provium run status RUN_ID --format json
provium run tasks RUN_ID --format json
provium run inputs RUN_ID --format json
provium run outputs RUN_ID --format json
```

These read durable snapshots and accepted results.

### Artifacts

```bash
provium run artifacts RUN_ID --locations --format json
```

Returns bindings by record/node/output field and optionally active locations.

### Cancel

```bash
provium run cancel RUN_ID --format json
```

Cancels a nonterminal run. Completed task boundaries remain durable.

### Export resolved configuration

```bash
provium run configuration export RUN_ID --output resolved.json
```

The file comes from the stored validated snapshot.

### Export complete bundle

```bash
provium run export RUN_ID --output run-bundle.json
```

The bundle includes the versioned run snapshot, tasks, inputs, outputs, dispatch history, and audit data available to the local export service.

## Dispatch commands

### Show

```bash
provium dispatch show DISPATCH_ID --format json
```

### Wait

```bash
provium dispatch wait DISPATCH_ID --format json
```

Polls durable state until the dispatch is terminal and also works across separate SQLite-opening processes.

### Cancel

```bash
provium dispatch cancel DISPATCH_ID --format json
```

### Retry

```bash
provium dispatch retry DISPATCH_ID --format json
```

Creates execution for only failed tasks from the original dispatch while preserving the original selection boundary and dependency policy. It does not expand to unrelated failed tasks in the run.

## Complete local workflow

Assuming finalized artifacts have already been loaded into managed indexing by the application/setup:

```bash
provium pipeline validate pipeline.yaml
provium input-set create inputs.ndjson --identifier demo

RUN_ID=$(provium run create pipeline.yaml \
  --input-set demo --all --format json \
  | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["run_id"])')

provium run execute "$RUN_ID" --all --format json
provium run status "$RUN_ID" --format json
provium run outputs "$RUN_ID" --format json
provium run artifacts "$RUN_ID" --locations --format json
provium run export "$RUN_ID" --output run-bundle.json
```

For automation, parse the JSON envelope and check the process exit code. Do not scrape human output.

## Diagnostics

Common actionable errors include:

- unsupported installed Provium core API version;
- unknown or duplicate catalog definitions;
- YAML/JSON schema path errors;
- pipeline compilation diagnostics;
- missing resolver options or invalid resolver configuration;
- invalid UUID identifiers;
- unknown run/input-set/dispatch;
- unsatisfied selected-only dependency;
- state transition conflict;
- artifact missing/corrupt/no active location;
- task output conflict.

A diagnostic should be resolved at the boundary it names. For example, a compilation artifact mismatch is a definition problem; a missing active location is storage health, not a reason to alter the pipeline graph.
