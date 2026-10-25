# Dispatch

`provium-dispatch` adds durable orchestration to [pipelines](pipelines.md): a registry, PostgreSQL queues and leases, compute profiles, persistent input-set names, and coordinated cache maintenance. Pipeline execution remains usable without dispatch.

## Install and configure

```bash
pip install 'provium-dispatch[postgres]'
```

The base `provium-dispatch` installation supports local input-set indexing. PostgreSQL is the supplied durable queue backend. Add `provium[s3]` when using S3 artifacts.

Create a PostgreSQL database and provide a connection URL. Dispatch initializes its schema automatically. A project `.provium/config.toml` can contain:

```toml
[dispatch]
database = "postgresql://USER:PASSWORD@HOST:5432/DATABASE"
store = "./.provium/artifacts"
# scratch_directory = "/tmp/provium"

[dispatch.queue]
default_enabled = true
default_group = "default"
```

Keep credentials out of Git. Dedicated `--database`, `--store`, and `--scratch-directory` flags override configuration. Dispatch reads `dispatch.*`; it does not fall back to `pipeline.*`. See [Configuration](configuration.md).

All executors need the installed procedure/artifact packages and access to queued input locations and the same artifact namespace. A local store must be a shared filesystem path for workers on different machines; S3 is another option. PostgreSQL stores coordination and result metadata, not artifact bodies.

## Register, enqueue, and execute

Use the `text-processing` graph and `document.pa` from the [pipeline tutorial](pipelines.md):

```bash
provium dispatch register -f pipeline.yaml
provium dispatch list pipelines
provium dispatch enqueue text-processing \
  --input document=document.pa --output word_count=word-count.pa
provium dispatch queue text-processing
provium dispatch run text-processing -v
provium dispatch status text-processing --outputs
```

Enqueue records inputs and destinations; registered execution consumes them. Do not supply `--input`, `--output`, `--csv`, or `-f` to a registered `run`. Local destination paths are normalized at admission, so a later worker does not reinterpret them relative to its own working directory.

For batches, use the same input and destination columns as basic execution:

```bash
provium dispatch enqueue text-processing --csv requests.csv
provium dispatch run text-processing --strategy stage
```

Both `--strategy stage` and `--strategy item` are supported. Stage mode favors reuse of loaded procedure setup across inputs; item mode advances one item at a time.

Enqueue without output destinations remains supported for cache-only queued work. Direct CLI execution requires destinations. Dispatch also supports direct `run -f pipeline.yaml --input ... --output ...`, including profile selection, without registering the graph.

Registration snapshots graph and contract information. Queued work retains its admitted graph and input identities; changing a registration does not silently redefine existing work. Input files must remain available and match their admitted identities when consumed.

## Compute profiles

Profiles are dispatch documents, separate from the graph:

```yaml
kind: ComputeProfile
name: local
workers:
  - default: 1
  - pipelines.text-processing.nodes.tokenize: 2
soft_time_limit: 30
hard_time_limit: 60
```

```bash
provium dispatch register -f profile.yaml
provium dispatch run text-processing --profile local
```

Worker counts must be positive integers. Time limits are positive finite seconds. Profiles also accept selector-based `soft_time_limits` and `hard_time_limits` lists. Selectors include `default`, `tags.TAG`, `pipelines.PIPELINE.tags.TAG`, and `pipelines.PIPELINE.nodes.NODE`; more specific rules take precedence. CLI `--workers`, `--only`, and `--force` supply execution overrides.

Time budgets control worker shutdown and cancellation handling; they do not provide a durable automatic retry policy. The runtime drains or stops workers according to the configured budgets. Check outcomes and queue state after interrupted work.

Applications can supply a dispatch `PipelineDefinition` with a `profiles` allowlist. An empty allowlist imposes no profile restriction. Basic pipeline definitions only describe input/output types.

## Named queues and retries

```bash
provium dispatch enqueue text-processing -q priority \
  --input document=document.pa --output word_count=priority-count.pa
provium dispatch run text-processing -q priority
provium dispatch queue text-processing -q priority
provium dispatch retry text-processing -q priority
```

Queue selection isolates admission, consumption, summaries, and retries. It does not create a separate artifact cache: equivalent computations can reuse results across queues. Queue names are validated, and `default` is selected when no override is supplied.

Set `dispatch.queue.default_enabled=false` to require an explicit queue. Direct file execution does not need queue selection.

Admission deduplicates equivalent queued requests, including their destinations. Re-enqueueing an existing request does not reset it. `retry` makes failed requests eligible again; already accepted computation results remain reusable. A changed destination is a distinct publication request, though its computations can hit cache.

Queue summaries expose states and counts. Pending, partial, cancelled, failed, and completed work have different meanings; a partial or cancelled request should not be treated as fully published. Computation leases and ownership generations prevent an expired worker from replacing a newer accepted result. Custom side effects inside procedures still need their own idempotence.

Remove registry entries explicitly:

```bash
provium dispatch unregister text-processing
provium dispatch unregister --profile local
```

`unregister -f FILE` selects entries named by documents. Registry removal is distinct from cache cleanup and input-set name removal.

## Name input sets and look up artifacts

An input-set name is a pipeline-independent alias for logical input bindings and their fingerprints:

```bash
provium dispatch input-set register my-document --input document=document.pa
provium dispatch input-set resolve my-document
provium dispatch input-set find --input document=document.pa
provium dispatch lookup text-processing.outputs.word_count --input-set my-document
provium dispatch lookup text-processing.nodes.tokenize.destination --input-set my-document --json
```

Use `--input-set-hash HASH` instead of a name, or `--input document=document.pa` to inspect an input directly. These alternatives are mutually exclusive. A hash lookup requires an indexed input set.

For a local, unregistered pipeline, provide its graph:

```bash
provium dispatch lookup text-processing.outputs.word_count \
  -f pipeline.yaml --input-set my-document
```

Without a database, names are stored at `<store>/input-sets.sqlite3`. Each operation owns its SQLite connection; connections are not shared between worker threads. With `--database`, the PostgreSQL backend supplies the index. An S3 store requires a database for persistent indexing.

The hash includes logical input names and artifact fingerprints, not filenames, locations, artifact UUIDs, pipeline names, or run IDs. Re-registering equivalent content does not make the hash depend on its new path. Index records retain locations; names are aliases, not a file relocation service.

```bash
provium dispatch input-set unregister my-document
# Remove aliases for an equivalent input set:
provium dispatch input-set unregister --input document=document.pa
```

Removing aliases preserves the indexed hash record and artifacts. Input-set names currently select lookup inputs; `run` and `enqueue` do not accept `--input-set`.

Lookup uses stored fingerprints and accepted results without fetching every intermediate artifact. It returns a URI or JSON metadata, not an exported copy, and does not compute missing results. A metadata record alone does not guarantee the bytes still exist. Use artifact APIs or materialization to read and verify the result.

## Cache maintenance and space

```bash
provium dispatch cache clear --older-than 86400
provium dispatch cache clear --max-bytes 1073741824
```

The local-file filters match [pipeline cleanup](pipelines.md#storage-and-cleanup). Dispatch requires its database when clearing a managed store. A `.dispatch-cache` marker prevents basic cleanup from bypassing durable coordination.

The backend checks queued demand and active work before deletion and holds an admission/maintenance guard across the operation. Result metadata is forgotten before bytes are removed, so an interrupted cleanup can leave unreferenced bytes rather than an accepted result pointing at intentionally deleted content.

Current protection is deliberately conservative: pending, partial, or cancelled jobs anywhere in the database can block cleanup, as can active readers and unexpired running leases. It does not yet select individual unused artifacts by tracing every queued job's provenance. This can retain more data than necessary.

`--discard-cache` performs guarded cleanup after successful execution, not incremental eviction during processing. Cleanup supports local file stores only. `--max-bytes` is a maintenance target, not an enforced runtime quota. If protected work fills storage, execution raises an out-of-space error; clearing retained eligible cache may help, but live dependencies are not deleted to make room.

## Python and custom backends

Use dispatch's runtime and preparation modules for durable work:

```python
from provium_dispatch.persistence.factory import Backend, open_backend
from provium_dispatch.preparation import prepare_pipeline
from provium_dispatch.runtime import ExecutionContext
from provium_pipeline.store import ArtifactStore


def main():
    backend = open_backend(
        "postgresql://localhost/provium", backend=Backend.Postgres
    )
    prepared = prepare_pipeline(name="text-processing", backend=backend)
    with ArtifactStore("./.provium/artifacts") as store:
        with ExecutionContext(
            prepared.plan.configuration,
            store,
            definition=prepared.definition,
            profile=prepared.profile,
            backend=backend,
            queue_group="default",
        ) as execution:
            execution.start()


if __name__ == "__main__":
    main()
```

Register and enqueue work first, for example through the CLI. Python APIs do not load CLI configuration. Imports should use owning modules; optional database drivers load when selected.

The [dispatch API reference](../reference/provium-dispatch.md) describes persistence interfaces. Core scheduling, cache, publication, and coordination protocols remain in [pipeline](../reference/provium-pipeline.md), so custom orchestration need not depend on the PostgreSQL implementation.

## Migrating older pipeline integrations

| Previous surface | Current surface |
| --- | --- |
| `provium pipeline register/unregister/list/enqueue/retry/queue` | Corresponding `provium dispatch` commands |
| Registered `pipeline run NAME` and `--profile` | `dispatch run NAME` and dispatch profiles |
| `pipeline input-set` and named lookups | `dispatch input-set` and `dispatch lookup` |
| `[pipeline].database`, `[pipeline.queue]` | `[dispatch].database`, `[dispatch.queue]` |
| `provium-pipeline[postgres]` | `provium-dispatch[postgres]` |
| `provium_pipeline.models.ComputeProfile` | `provium_dispatch.models.ComputeProfile` |
| Pipeline persistence imports | `provium_dispatch.persistence.*` |
| Basic runtime `profile` / `backend` | `settings` / `coordinator`; durable runtime retains `profile` / `backend` |
| Graph YAML `outputs` | Prefer `targets`; old key remains accepted |
| Publisher `validate` | `assert_requests_are_valid` |

Move dispatch store/scratch defaults into `dispatch.*` too. Keep `pipeline.*` for direct basic execution. The two groups have independent configuration sections even when they intentionally share one store.
