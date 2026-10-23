# Pipelines

Provium Pipeline connects installed Provium procedures into validated graphs.
It executes input sets, reuses verified results, and records provenance for each
output. Start with local files; add PostgreSQL for persistent queues and
coordination between processes, and S3 for remote artifact storage.

## Install

Python 3.12 or newer is required. The base package includes local execution and
filesystem storage:

```sh
python -m pip install provium-pipeline
```

Install only the backends you use:

```sh
python -m pip install 'provium-pipeline[postgres]'
python -m pip install 'provium-pipeline[s3]'
python -m pip install 'provium-pipeline[postgres,s3]'
```

All commands are registered with the core CLI:

```sh
provium pipeline --help
python -m provium pipeline --help
```

There is no separate `provium-pipeline` command. Install the procedure and artifact
catalogs referenced by your graph on every machine that will execute it.

## Run your first graph

The example below uses the text plugin included in the repository. From a checkout:

```sh
python -m pip install -e packages/provium -e packages/provium-pipeline \
  -e examples/provium-text-pipeline
printf 'Hello from Provium\n' > document.txt
provium artifact load provium_text_pipeline_example.DocumentV1 document.txt document.pa
```

Pipeline inputs are existing Provium artifacts, not raw files. Save this graph as
`pipeline.yaml`:

```yaml
kind: PipelineConfiguration
name: tokenize-documents
inputs:
  document: provium_text_pipeline_example.DocumentV1
nodes:
  tokenize:
    procedure: provium_text_pipeline_example.TokenizeV1
    tags: [text]
    inputs:
      source: $inputs.document
outputs:
  tokens: $nodes.tokenize.destination
```

Validate the graph, inspect its plan, and execute it:

```sh
provium pipeline validate -f pipeline.yaml
provium pipeline inspect -f pipeline.yaml
provium pipeline run -f pipeline.yaml --input document=document.pa --store ./artifacts
provium pipeline status -f pipeline.yaml --input document=document.pa --store ./artifacts
```

Repeated equivalent work trusts committed artifact metadata by default, so cached
steps do not fetch or read intermediate or final artifact files. Their recorded
content fingerprints determine downstream computation keys. Original pipeline
inputs are still inspected and verified. Cached artifacts are fetched and verified
when a procedure needs to consume them. Use `pipeline run --verify-artifacts` to
verify cached files even when no procedure needs to run. Missing or corrupt cached
files are otherwise not detected during a fully cached run. `pipeline status`
continues to verify artifacts when inspecting outputs.

Repeated equivalent work reuses cached artifacts. `pipeline run` prints a
readable summary by default; add `--json` for structured results on standard
output, including error details for failed input sets. Runs stream diagnostics
to standard error; redirect stderr with
`2>run.log` to save them. No run log file is created automatically. Validation
and plan inspection do not create an artifact store or start worker processes.
Status checks existing results without computing missing work.

A run returning 0 means the executor completed normally; individual input sets
can still be failed, partial, or cancelled. Inspect the outcomes and run log to
determine whether every item succeeded. Invalid configuration or an executor
failure returns a nonzero exit status.

## Describe a graph

Every graph declares `inputs`, `nodes`, and `outputs`; use an empty mapping for
an empty section. Input names and artifact identifiers must match submitted
artifacts. Each node refers to an installed procedure identifier.

| Field | Purpose |
| --- | --- |
| `inputs` | Bind procedure inputs to `$inputs.name` or `$nodes.node.output`. |
| `setup_inputs` | Bind reusable setup inputs, such as model weights. |
| `configuration` | Supply the procedure's configuration values. |
| `tags` | Group nodes for filtering and compute settings. |

Repeated procedure inputs accept lists of references. Optional inputs can be
omitted or null. Graphs must be acyclic; all nodes must contribute to a declared
output. Procedure contracts validate bindings, cardinalities, and artifact types.

Repeat `-f` to load several files, or separate YAML documents with `---`.
Duplicate YAML keys and duplicate document kind/name pairs are rejected.

## Choose compute settings

Compute profiles are independent of graph definitions. Save `profiles.yaml`:

```yaml
kind: ComputeProfile
name: local
workers:
  - default: 1
  - tags.text: 2
soft_time_limit: 3600
hard_time_limit: 3660
```

```sh
provium pipeline run -f pipeline.yaml -f profiles.yaml --profile local \
  --input document=document.pa --workers nodes.tokenize=3 --store ./artifacts
```

Worker counts must be positive integers. More specific node settings override
tag settings, which override defaults. Profile node selectors include the graph
name, such as `pipelines.tokenize-documents.nodes.tokenize`; CLI overrides can
use `nodes.tokenize`. Multiple workers benefit runs containing multiple input
sets. Each execution context keeps workers for one configured node resident at
a time.

Limits are measured in seconds. Soft limits stop new work and drain running
invocations. Hard limits terminate affected workers. Profiles also support
`soft_time_limits` and `hard_time_limits` lists of selector/value mappings for
node-specific limits.

Use `--only nodes.tokenize` or `--only tags.text` to select work. Filters can
leave an input set partially complete. Use `--force nodes.tokenize`,
`--force tags.text`, or `--force all` to replace cached results for selected
work. Previously accepted results remain available if replacement fails.

## Queue work with PostgreSQL

Install the `postgres` extra and configure a connection:

```sh
export PROVIUM_DATABASE_URL=postgresql://localhost/provium
provium pipeline register -f pipeline.yaml -f profiles.yaml
provium pipeline enqueue tokenize-documents --input document=document.pa
provium pipeline run tokenize-documents --profile local --store ./artifacts
provium pipeline status tokenize-documents --outputs
```

`--database` overrides the environment variable. First use initializes or
migrates the schema. Registration validates documents and rejects conflicting
objects with the same name; identical registration is a no-op.

Enqueue pins the graph and input artifact identities. Repeating the same
submission returns its existing queue ID without resetting its state. A
registered run takes inputs from the queue and cannot also accept `-f` or
`--input`. Use `--idle-wait 30` to wait for additional work after the queue drains.

| Command | Purpose |
| --- | --- |
| `list [pipelines\|profiles]` | List registered identities. |
| `queue [pipeline]` | Summarize queued input states. |
| `status pipeline` | Show persisted input details; add `--outputs` to inspect artifacts. |
| `retry pipeline` | Reset failed queued inputs for a later run. |
| `unregister pipeline` | Remove the registration and its input queue/history. |
| `unregister --profile local` | Remove a registered profile. |
| `unregister -f pipeline.yaml` | Remove the objects named in the document. |

Unregistration preserves shared artifacts and computation results. It is not a
hard cancellation command: already-admitted work can finish.

Multiple executors must share a database and the same artifact-store namespace
to coordinate computation leases. Use a shared filesystem or S3 across hosts.
Do not mix direct, uncoordinated writers with queued writers to the same store.
Schedulers such as Slurm or AWS Batch can launch `provium pipeline run`;
scheduler submission itself is managed outside Provium.

## Store artifacts locally or in S3

Store selection is `--store`, then `PROVIUM_PIPELINE_STORE`, then
`./.provium/artifacts`. Install the `s3` extra for S3 inputs, stores, or YAML
configuration files:

```sh
provium pipeline run -f s3://my-bucket/pipeline.yaml \
  --input document=s3://my-bucket/document.pa \
  --store s3://my-bucket/artifacts --scratch-directory /tmp/provium
```

S3 uses boto3's normal credential and region configuration. Temporary downloads
use the system temporary directory unless `--scratch-directory` overrides it.
See [Local and S3 files](storage.md) for file handling and publication behavior.

## Use Python

Use an importable script with a main guard because execution spawns child
processes:

```python
from provium_pipeline.preparation import prepare_pipeline
from provium_pipeline.runtime import ExecutionContext, InputSet
from provium_pipeline.store import ArtifactStore


def main():
    prepared = prepare_pipeline(files=["pipeline.yaml"])
    with ArtifactStore("./artifacts") as store:
        with ExecutionContext(
            prepared.plan.configuration, store, definition=prepared.definition
        ) as execution:
            execution.start([InputSet({"document": "document.pa"})])
        for outcome in execution.outcomes:
            print(outcome.state, outcome.error)
            for name, artifact in outcome.outputs.items():
                print(name, artifact.location)


if __name__ == "__main__":
    main()
```

`start()` returns immediately; context exit joins execution. Optional
`on_step_finished` and `on_pipeline_finished` callbacks receive an `Outcome`.
Callbacks run serially on a dispatcher thread; keep them short. Call
`execution.cancel()` to stop admissions and drain running work.

See the [Pipeline API reference](../reference/provium-pipeline.md) for
definitions, preparation, persistence, and extension points.

## Named queues

Registered pipelines can have multiple named queues. Use `--queue NAME` or `-q NAME`
to select one for enqueueing, execution, status, queue summaries, and retries:

```bash
provium pipeline enqueue documents -q priority --input document=report.pa
provium pipeline run documents -q priority
provium pipeline status documents -q priority --outputs
provium pipeline queue documents -q priority
provium pipeline retry documents -q priority
```

Queues are created implicitly on enqueue. Names are case-sensitive, nonempty
strings without leading or trailing whitespace. Each command selects exactly one
queue. Omitting a pipeline name from `pipeline queue` lists that queue's counts
across registered pipelines, including empty queues.

Deduplication is per pipeline and queue, so the same input can be queued independently
in `priority` and `batch`. Workers, status, and retries operate only on the selected
queue. Computation leases and cached artifacts remain shared: queue isolation does
not force equivalent work to run again. Unregistering a pipeline removes its entries
from every queue, while retaining shared computation results.

Without an explicit queue, the CLI uses these defaults:

```toml
[pipeline.queue]
default_enabled = true
default_group = "default"
```

Set `default_enabled = false` to require `--queue` for queued operations.
This disables implicit selection, not access to the queue named `default`.
Explicit queue names still work. Local file-based execution does not require
a queue and rejects `--queue`; enqueue/retry with `-f` still target a persisted queue.

Settings use the normal user/project configuration and override rules:

```bash
export PROVIUM_CONFIG__PIPELINE__QUEUE__DEFAULT_GROUP=batch
provium pipeline run documents --config pipeline.queue.default_group=priority
```

The PostgreSQL migration assigns existing entries to `default` without changing
their IDs, states, or deduplication keys. Stop older workers before upgrading;
older clients do not understand the new queue-scoped uniqueness constraint.
Migration runs automatically when opening the backend.

## Named input sets and artifact lookup

Register a set independently of any pipeline or run:

```sh
provium pipeline input-set register my-document --input document=document.pa
provium pipeline input-set resolve my-document
provium pipeline input-set find --input document=document.pa
provium pipeline input-set unregister my-document
provium pipeline input-set unregister --input document=document.pa
```

Names refer to a stable hash of logical input names and type/provenance/content
fingerprints. File paths, filenames, artifact UUIDs, pipeline names, and run IDs
are excluded. Binding order does not matter. Multiple names can identify the
same set. Re-registering identical inputs is a no-op; changing the inputs under
an existing name is rejected. The first recorded locations are retained.

Look up intermediate or final artifacts without running procedures:

```sh
provium pipeline lookup text-processing.nodes.tokenize.destination -f examples/provium-text-pipeline/pipeline.yaml --input document=example_document.pa
provium pipeline lookup text-processing.outputs.word_count -f examples/provium-text-pipeline/pipeline.yaml --input-set my-document
```

Omit `-f` for a registered pipeline. Select exactly one of `--input NAME=FILE`
(repeat for multiple inputs), `--input-set NAME`, or `--input-set-hash HASH`.
Hash lookup requires a previously indexed set. Human-readable lookup prints
just the URI; `--json` also returns the input-set hash and artifact fingerprint.
Input-set commands also support `--json`.

Lookup follows only the selected output's dependencies and reads committed
manifests, never cached artifact bodies. Direct input files are inspected;
named/hash lookup uses saved fingerprints without reopening the input files.
Missing results or invalid references return a concise error and exit status 2.
Recorded URIs may refer to files that were subsequently removed or changed.
The selected graph and current procedure contracts determine the computation.

Without a configured database, the index is `input-sets.sqlite3` inside the
artifact store, which defaults to `./.provium/artifacts`. Each operation opens
and closes its own SQLite connection; concurrent writers serialize through
SQLite transactions. With `pipeline.database`, the index lives in PostgreSQL
and is shared across pipelines and stores. Named sets in S3 require PostgreSQL.
Use the same `--store` as execution. File-based lookup reads result manifests;
registered-pipeline lookup reads database results.

Unregistration removes names only, preserving hash records, artifacts, results,
and queued work. Registration does not enqueue or execute anything.

## Extended text example

The repository's `examples/provium-text-pipeline/pipeline.yaml` has no compute
profile. It tokenizes a document, then counts the total number of tokens using
`CountWordsV1`, publishing `tokens` and `word_count`.

```sh
provium pipeline run -f examples/provium-text-pipeline/pipeline.yaml --input document=example_document.pa -v
```
