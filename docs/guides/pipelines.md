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

Repeated equivalent work reuses verified artifacts. Commands print JSON to
standard output. A run also reports its log path on standard error. Validation
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
