# Pipeline usage examples

These examples use the implemented API. Start with existing Provium artifacts; importing raw data is a separate core operation. Install core, pipeline, and the example procedure catalog from this checkout:

```sh
python -m pip install -e packages/provium -e packages/provium-pipeline \
  -e examples/provium-text-pipeline
```

## Direct execution, without a database

Create `pipeline.yaml`:

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

The top-level `inputs` mapping declares every required input name and its artifact identifier. Input sets must match these names and types exactly, including inputs passed directly to outputs. Node bindings can only reference declared inputs. Use `inputs: {}` for a pipeline that requires no inputs. When supplied, a Pipeline Definition must agree with the declared input names and types.

Existing configurations must add this mapping before validation or registration. Previously registered configurations and queued snapshots without it must be replaced explicitly; this change does not rewrite persisted work.

`DOCUMENT_ARTIFACT` is the path or S3 URL of an existing Document artifact produced with named provenance bindings:

```sh
provium-pipeline run -f pipeline.yaml \
  --input "document=$DOCUMENT_ARTIFACT" --store ./artifacts
```

The equivalent installed core command is `provium pipeline run ...`. One child process prepares and executes the procedure. Repeating the command reuses verified artifacts. Standard output contains JSON outcomes; standard error reports the run-log path.

Store precedence is `--store`, then `PROVIUM_PIPELINE_STORE`, then `./.provium/artifacts`. Relative paths, absolute paths, `file://` URLs, and `s3://bucket/prefix` are supported.

```sh
export PROVIUM_PIPELINE_STORE=s3://my-bucket/artifacts
provium-pipeline run -f pipeline.yaml \
  --input "document=$DOCUMENT_ARTIFACT" --scratch-directory /local/scratch
```

S3 uses the normal AWS credential provider chain. Scratch defaults to the system temporary directory. The CLI removes its downloaded/staged files on shutdown. S3 checks consult S3 and download artifacts for verification.

### Filters, workers, and force

```sh
provium-pipeline run -f pipeline.yaml \
  --input "document=$DOCUMENT_ARTIFACT" \
  --only tags.text \
  --workers default=1 --workers tags.text=2 --workers nodes.tokenize=3
```

Filters form a union of configured node names and node tags. Worker settings resolve default → matching tags in order → exact node. More specific settings win regardless of flag order. Counts are positive integers; default is one process. Counts apply to each context, so several independently launched contexts each get their own workers.

A single input set usually cannot use multiple workers for one node. Programmatic execution and the persistent queue support multiple input sets. Only one configured node is resident per context; its workers reuse prepared procedure instances while draining ready input sets.

```sh
provium-pipeline run -f pipeline.yaml \
  --input "document=$DOCUMENT_ARTIFACT" --force nodes.tokenize
# Also supported: --force all, --force tags.text
```

Force replaces the result without comparing it to the old content. Changed content changes downstream provenance; identical content allows downstream reuse. Failed force preserves the previous result. Force respects filters and leases.

### Inspect without executing

```sh
provium-pipeline validate -f pipeline.yaml
provium-pipeline inspect -f pipeline.yaml --workers nodes.tokenize=2
provium-pipeline status -f pipeline.yaml \
  --input "document=$DOCUMENT_ARTIFACT" --store ./artifacts --outputs --json
```

Validation/inspection resolves standard procedure contracts without loading their implementations. Status reports node states and final output locations; it never computes missing work. Output is JSON, including when `--json` is omitted. A status result has this shape (hashes abbreviated):

```json
[{
  "state": "completed",
  "node": null,
  "reused": false,
  "outputs": {
    "tokens": {
      "location": "file:///work/artifacts/artifacts/<computation>/<generation>/<digest>.pa",
      "type": "<artifact identifier>",
      "provenance": "<producing provenance>",
      "content": "<body digest>"
    }
  },
  "error": null,
  "nodes": {
    "tokenize": {
      "state": "completed",
      "outputs": {
        "destination": {
          "location": "file:///work/artifacts/artifacts/<computation>/<generation>/<digest>.pa",
          "type": "<artifact identifier>",
          "provenance": "<producing provenance>",
          "content": "<body digest>"
        }
      }
    }
  }
}]
```

There is no verbose naming mode. Logical names come from status; physical generation paths keep readers safe during forced replacement.

## Compute profiles and pipeline definitions

Create `profiles.yaml`:

```yaml
kind: ComputeProfile
name: local
workers:
  - default: 1
---
kind: ComputeProfile
name: cluster
workers:
  - default: 1
  - tags.text: 2
  - pipelines.tokenize-documents.nodes.tokenize: 4
soft_time_limit: 3600
hard_time_limit: 3660
soft_time_limits:
  - tags.text: 300
hard_time_limits:
  - pipelines.tokenize-documents.nodes.tokenize: 360
```

```sh
provium-pipeline run -f pipeline.yaml -f profiles.yaml --profile cluster \
  --input "document=$DOCUMENT_ARTIFACT"
```

Files may contain multiple documents. All documents are loaded before selection; duplicate `(kind, name)` documents and duplicate YAML keys are errors. Profiles are separate from the graph. Scoped selectors start with `pipelines.`; global and scoped tags share the same precedence.

All limits use seconds. Node timers count active wall time, shared across that node's workers, and retain their accumulated total across switches. Global timers include idle time. Soft limits stop new admissions and drain in-flight work. Hard limits terminate affected worker processes; the coordinator remains alive to preserve publication integrity. Limits resolve independently, with null clearing an inherited limit.

An application can supply its simple input/output definition in `application.py`:

```python
from provium_pipeline import PipelineDefinition
from provium_text_pipeline_example.artifact import DOCUMENT, TOKENS

definition = PipelineDefinition(
    inputs={"document": DOCUMENT},
    outputs={"tokens": TOKENS},
    profiles=frozenset({"local", "cluster"}),
)
```

Select it with `--definition application:definition` on direct commands or registration. Alternatively install an entry point named after the pipeline configuration:

```toml
[project.entry-points."provium.pipeline.definitions"]
tokenize-documents = "application:definition"
```

Definitions restrict input/output types and allowed profile names. Without a supplied/discovered definition, graph wiring is still checked against standard procedure contracts, but there is no application-level profile allowlist.

Procedures remain ordinary core procedures. Optional invocation/setup inputs may be omitted or null; repeated inputs use a list of references. Setup dependencies, such as weights, use `setup_inputs`. Procedure configuration goes under each node's `configuration`, and tags under `tags`. Branches reuse references; joins bind multiple upstream outputs. Compute settings never enter provenance.

## Programmatic execution and callbacks

```python
from provium_pipeline import (
    ArtifactStore, ComputeProfile, ExecutionContext, InputSet,
    PipelineConfiguration, load_documents,
)
from application import definition

def step_finished(outcome):
    print(outcome.node, outcome.state, "cached:", outcome.reused)
    if outcome.error:
        print(outcome.error)

def pipeline_finished(outcome):
    print("Input:", outcome.input_set.inputs, "state:", outcome.state)
    for name, artifact in outcome.outputs.items():
        print(name, artifact.location)

def main():
    documents = load_documents(["pipeline.yaml", "profiles.yaml"])
    graph = next(d for d in documents if isinstance(d, PipelineConfiguration))
    profile = next(
        d for d in documents
        if isinstance(d, ComputeProfile) and d.name == "local"
    )
    inputs = [InputSet({"document": "/data/one.pa"}),
              InputSet({"document": "/data/two.pa"})]
    with ArtifactStore("./artifacts") as store:
        with ExecutionContext(
            graph, store, definition=definition, profile=profile,
        ) as execution:
            execution.start(
                inputs,
                on_step_finished=step_finished,
                on_pipeline_finished=pipeline_finished,
            )
            # start returns immediately. Do other application work here.
        # Context exit joins execution. Callbacks run while processing proceeds.
        for outcome in execution.outcomes:
            print(outcome.state)

if __name__ == "__main__":
    main()
```

The main guard is required for process spawning. Procedures must be importable by child processes. Installed core procedure catalogs are discovered automatically; callers may supply `procedures={identifier: definition}` explicitly.

Both callbacks receive an `Outcome` with the original `InputSet`, state, outputs, optional node/error, and reuse flag. Fresh and cached steps notify consistently. Overall states are `completed`, `partial`, `failed`, or `cancelled`. Each logical step notifies once per input-set occurrence in a run, including duplicate input sets.

Callbacks run serially on a context-owned dispatcher thread. Keep them short: the queue holds 1,024 events, overflow is logged, and shutdown allows bounded callback draining. Delivery is best effort; persistent state is authoritative. Callback exceptions are logged and never rerun a procedure.

`execution.cancel()` stops admissions and drains current invocations. Use a profile hard limit to bound non-cooperative procedure execution. `execution.finished` exposes completion; no result iterator or public wait method is needed. A context starts once. Executor failure raises when the context closes; individual failures remain outcomes.

`Artifact.location` is durable. For S3, `Artifact.path` points to a verified local download owned by the store; use it before closing the store. Closing a store removes only its downloads. Filesystem artifact paths remain valid.

YAML files supplied with `-f` may also use `s3://bucket/key` locations. Local and
remote files can be mixed, and `--scratch-directory` controls temporary downloads.
See [Local and S3 files](guides/storage.md) for core and pipeline examples.

## PostgreSQL-backed execution

```sh
export PROVIUM_DATABASE_URL=postgresql://localhost/provium
provium-pipeline register -f pipeline.yaml -f profiles.yaml \
  --definition application:definition
provium-pipeline enqueue tokenize-documents --input "document=$DOCUMENT_ARTIFACT"
provium-pipeline run tokenize-documents --profile cluster \
  --store s3://my-bucket/artifacts --idle-wait 30
provium-pipeline status tokenize-documents
provium-pipeline status tokenize-documents --outputs --json
```

`--database` overrides the environment variable. First use initializes/migrates the schema automatically under a PostgreSQL migration lock. Registration validates the whole batch transactionally. Identical objects are a no-op; conflicting names are rejected. Use a new name for a changed graph/profile.

Enqueue pins the graph, optional definition, and verified named input identities/content. Repeating a submission returns its existing queue ID without resetting state. A queue has no profile. Registered `run` accepts neither YAML files nor direct inputs.

Ready work is prioritized by stable topological/declaration order across input sets. Scanning uses bounded pages. Multiple contexts sharing the same database and canonical store namespace compete for provenance leases, including across differently named pipelines. Heartbeats continue while child processes run.

When work drains, run exits immediately unless `--idle-wait` supplies a waiting period in seconds. Filters may intentionally leave partial work. Failed computations do not retry in the same invocation. To explicitly reopen failed queued input sets for a later run:

```sh
provium-pipeline retry tokenize-documents
provium-pipeline run tokenize-documents
```

Programmatic queued execution uses the same context:

```python
from provium_pipeline import PostgresBackend

backend = PostgresBackend("postgresql://localhost/provium")
graph = PipelineConfiguration.model_validate(
    backend.get("PipelineConfiguration", "tokenize-documents")
)
with ArtifactStore("./artifacts") as store:
    with ExecutionContext(graph, store, backend=backend, idle_wait=30) as execution:
        execution.start(
            on_step_finished=step_finished,
            on_pipeline_finished=pipeline_finished,
        )
```

Run exit code 0 means the executor operated normally, including empty queues, failed items, partial completion, and handled time limits. Invalid commands/configurations or executor failure return nonzero. Item failure details and tracebacks are in the run log, whose default location is the system temporary directory; programmatic callers may set `log_path`.

## Managing registered pipelines and queues

List objects registered in the selected PostgreSQL database:

```sh
provium-pipeline list
provium-pipeline list pipelines
provium-pipeline list profiles
provium-pipeline queue
provium-pipeline queue tokenize-documents
```

These commands return JSON and accept `--database` or `PROVIUM_DATABASE_URL`.
`list` reports registered kind/name pairs; it does not enumerate installed Python
procedure packages. `queue` reports total InputSets and counts by persisted state
(pending, partial, cancelled, failed, completed), including empty registered queues.
It does not load procedures or inspect artifact files. A pending count is not a
count of active workers: execution ownership is tracked separately by computation
leases. Use `status <pipeline>` for per-InputSet details and `status --outputs` to
locate artifacts.

Remove a pipeline, a profile, or all objects named in YAML documents:

```sh
provium-pipeline unregister tokenize-documents
provium-pipeline unregister --profile cluster
provium-pipeline unregister -f pipeline.yaml -f profiles.yaml
```

File selection supports multi-document YAML and uses each document's `kind` and
`name`; no procedure implementation is loaded. Use either files or explicit names.
A pipeline and profile can share a name; the positional name selects the pipeline,
and `--profile` selects the profile. Both can be supplied in one invocation.

Unregistering a pipeline atomically deletes its saved definition and all its
InputSet records, including pending work and completed/failed queue history.
Unregistering a profile does not remove pipeline queues. Missing objects are a
successful no-op, so repeated commands are safe. The response reports
`unregistered` and `removed_inputs` counts.

Shared artifacts, accepted results, and computation leases are preserved.
Already-admitted work may finish and publish its result, but removed InputSets
cannot acquire new computation leases. Removing a registration is not a hard
cancellation command. You can register the name again afterward; its old queue
history will not be restored.

The same operations are available programmatically:

```python
backend.registered()                          # All registered identities
backend.registered("PipelineConfiguration")  # Pipelines only
backend.queue_info("tokenize-documents")      # Omit name for all queues
backend.unregister([
    {"kind": "PipelineConfiguration", "name": "tokenize-documents"},
    {"kind": "ComputeProfile", "name": "cluster"},
])
```

## Deployment and publication

Slurm and AWS Batch launch the same registered `run` command. Install compatible core/procedure packages on every node, provide database/network credentials, and use shared filesystems or S3 for artifacts. Allocate hardware in Slurm/Batch configuration; worker counts are per launched context. These are externally managed deployments, not built-in submission adapters.

Outputs are verified, written to immutable generation locations, then accepted together through one result reference. Direct runs atomically replace a store manifest. Queued runs atomically replace PostgreSQL's authoritative result under a live owner/generation check. A stale uploader can leave unreferenced objects but cannot replace a newer accepted result. Multi-output visibility is atomic at that reference.

Direct mode takes a host-local single-writer lock. Cross-host concurrent work must use PostgreSQL; do not mix coordinated and uncoordinated writers. Lost acknowledgements are reconciled without re-executing. Physical exactly-once execution through arbitrary crashes is not promised; stale publication is rejected. Garbage collection of abandoned/old artifact generations is deferred.

Core's versioned semantic provenance excludes execution UUIDs and operational settings. Legacy artifacts without named bindings remain readable by core but cannot be used for verified pipeline reuse; regenerate them with current core rather than guessing their producing provenance.
