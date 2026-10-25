# Pipeline API reference

The basic package owns graph execution and extension contracts. Durable registry and queue APIs live in [Dispatch](provium-dispatch.md). See the [pipeline guide](../guides/pipelines.md) for runnable workflows.

## Models and planning

| Import | Purpose |
| --- | --- |
| `provium_pipeline.models.PipelineConfiguration` | Typed graph, inputs, nodes, and public outputs; YAML accepts `targets` or legacy `outputs`. |
| `provium_pipeline.models.Node` | Procedure identifier, configuration, input/setup bindings, and tags. |
| `provium_pipeline.models.ExecutionSettings` | Worker and time-budget rules without a named compute profile. |
| `provium_pipeline.models.PipelineDefinition` | Frozen application input/output contract. |
| `provium_pipeline.documents.load_documents` | Local/S3 YAML loading. |
| `provium_pipeline.planning.build_plan` | Graph/contract validation and execution order. |

```python
from provium_pipeline.models import PipelineDefinition

definition = PipelineDefinition(
    inputs={"document": "provium_text_pipeline_example.DocumentV1"},
    outputs={
        "tokens": "provium_text_pipeline_example.TokensV1",
        "word_count": "provium_text_pipeline_example.WordCountV1",
    },
)
```

Contracts accept artifact identifiers or definitions. Select one with `--definition application:definition`, or publish a graph-named entry point:

```toml
[project.entry-points."provium.pipeline.definitions"]
text-processing = "application:definition"
```

`provium_pipeline.definitions.load_definition(name, reference)` resolves an explicit reference or discovers the named entry point. No match returns `None`; duplicate matches fail. Basic definitions have no profile allowlist.

## Preparation

`provium_pipeline.preparation.prepare_pipeline` accepts keyword-only `files`, `scratch_directory`, `definition_reference`, `workers`, `only`, and `force`. It selects exactly one graph and returns a `PreparedPipeline` with `plan`, `definition`, `overrides`, and resolved `settings`; `summary()` produces JSON-compatible planning information. It does not start workers or open a database.

`load_pipeline(*, files=(), scratch_directory=None)` selects the graph without constructing its execution plan. Registered names and profiles require dispatch preparation.

::: provium_pipeline.preparation.prepare_pipeline

## Requests and publication

`provium_pipeline.requests.InputSet(inputs)` is the input-only request form, retained for programmatic cache-only execution. `ExecutionRequest(inputs, destinations)` adds a nonempty destination mapping. Input and destination mappings are snapshotted. Destinations use public target names or `nodes.NODE.PORT`.

`RequestSource` is an iterable yielding `ExecutionRequest` objects. `CSVSource(stream)` implements it for caller-owned text streams, with `inputs.NAME` headers and destination headers. Empty destination cells omit publication for that row. Source adapters should produce the same validated request objects rather than introduce format-specific scheduling.

`provium_pipeline.publication.Publisher` defines:

| Method | Contract |
| --- | --- |
| `assert_requests_are_valid(requests)` | Validate the complete request collection before execution. |
| `publish(artifact, destination, *, overwrite=False)` | Materialize/publish an artifact and return its destination reference. |

`FilePublisher(store)` supplies local/S3 artifact copying and destination compatibility checks. Custom publishers own destination validation and overwrite semantics. Publication can partially succeed; a failure must not be reported as a successful request.

::: provium_pipeline.requests.ExecutionRequest

## Execution lifecycle

`provium_pipeline.runtime.ExecutionContext(configuration, store, ...)` accepts:

| Option | Meaning |
| --- | --- |
| `definition`, `procedures` | Application contract and optional procedure definitions. |
| `settings`, `workers` | Execution settings and worker overrides. |
| `only`, `force` | Execution eligibility and forced recomputation selectors. |
| `coordinator` | Optional computation ownership/result implementation. |
| `idle_wait` | Idle polling delay. |
| `log_path` | Explicit log file; omitted logs go to stderr. |
| `verify_artifacts=False` | Eagerly verify cached output bodies on reuse. |
| `strategy="stage"` | `stage` or `item` scheduling. |
| `targets`, `publisher` | Target selection and publication implementation. |

Call `start(input_sets=(), *, on_step_finished=None, on_pipeline_finished=None)` inside the context. It starts once and returns immediately. Context exit joins workers and can raise executor failures. Individual request failures are represented in `execution.outcomes`.

`Outcome` contains `input_set`, `state`, `outputs`, `node`, `reused`, and `error`. Callbacks are serialized through dispatching and are best effort; do not use callbacks as the sole durable record.

::: provium_pipeline.runtime.ExecutionContext

::: provium_pipeline.runtime.Outcome

## Artifact cache

`provium_pipeline.store.ArtifactStore(location, *, scratch=None)` implements `ArtifactCache` for local/S3 artifacts. Use it as a context manager. An `Artifact` carries `location`, optional local `path`, `identity`, and `fingerprint`. Cached references can have `path=None`.

| ArtifactCache member | Responsibility |
| --- | --- |
| `namespace`, `scratch`, `root` | Stable coordination namespace, local staging directory, optional local root. |
| `writer_lock(*, shared=False)` | Scoped store exclusion. |
| `inspect(location)` | Inspect and verify an input artifact. |
| `stage(computation, paths, expected)` | Store output artifacts and return durable metadata. |
| `commit(computation, outputs)` | Accept a direct result manifest. |
| `manifest(computation)` | Read a direct accepted result, if any. |
| `restore(outputs, expected)` | Reconstruct references from metadata without body reads. |
| `materialize(artifact)` | Obtain a local, verified artifact for consumption. |
| `verify(outputs, expected)` | Eagerly materialize and verify outputs. |

Downloaded paths belong to the store and expire when it closes. A custom cache can store bytes elsewhere, including a database, but must preserve identity verification, accepted-result consistency, and local materialization for procedure readers. No database-blob cache is supplied.

::: provium_pipeline.store.ArtifactCache

## Coordination and maintenance

`provium_pipeline.coordination.Coordinator` separates computation ownership from byte storage:

- `result(namespace, key)` reads accepted outputs.
- `acquire(namespace, key, run, *, force=False, ttl=60, input_id=None)` returns a lease or `None`.
- `renew(lease, *, ttl=60)` extends ownership.
- `complete(lease, outputs)` conditionally accepts results.
- `fail(lease, error)` records failure.

A `Lease` identifies namespace, key, owner, generation, and run. Implementations must fence stale owners when completing work. Supplying a coordinator replaces direct store-level execution locking; the integration must supply appropriate exclusion and maintenance coordination.

`WorkQueue.pending(...)` and `progress(identity, state, detail)` define queue consumption separately. Durable registration/admission extends these contracts in dispatch.

`provium_pipeline.cache.CacheInventory` provides `guard()`, `entries()`, and `remove(key)`. `FileCache(store)` implements local inventory. Entries expose computation `key`, byte `size`, and modification timestamp. `MaintenanceGuard.hold(namespace)` excludes conflicting activity; `forget(namespace, keys)` invalidates accepted results before removal. A custom backend must keep admission and cleanup mutually safe, not merely check for readers before taking an unrelated lock.

::: provium_pipeline.cache.CacheInventory

::: provium_pipeline.cache.MaintenanceGuard

## Input fingerprints and lookup

`provium_pipeline.input_sets.inspect_inputs(store, bindings)` returns verified named location/identity/fingerprint records. `input_set_hash(records)` hashes logical names and fingerprints only.

`provium_pipeline.lookup.lookup_artifact(plan, store, inputs, selector, *, backend=None)` resolves `pipeline.nodes.node.port` or `pipeline.outputs.alias`. The optional `ResultReader` backend supplies `result(namespace, key)`; otherwise lookup reads store manifests. It returns an artifact reference without materializing its body and raises when a required result is unavailable. Persistent input-set indexes belong to dispatch.

::: provium_pipeline.lookup.lookup_artifact

## CLI surface

| Command | Purpose |
| --- | --- |
| `pipeline validate -f FILE` | Validate graph and contracts. |
| `pipeline inspect -f FILE` | Inspect prepared execution. |
| `pipeline status -f FILE --input NAME=PATH` | Inspect reusable computation state; `--outputs` includes output information. |
| `pipeline run -f FILE --input NAME=PATH --output TARGET=PATH` | Execute and publish one request. |
| `pipeline run -f FILE --csv FILE` | Execute per-row inputs/destinations. |
| `pipeline lookup REF -f FILE --input NAME=PATH` | Locate cached intermediate or public output. |
| `pipeline cache clear` | Clear eligible local computation entries. |

Storage flags are `--store` and `--scratch-directory`; application defaults use `pipeline.*`. `--json` is available for structured output. See command `--help` for the full installed option set. `--outputs` is an inspection flag; `--output TARGET=PATH` requests publication.

Commands are core `Command` classes contributed through `provium.cli.command_catalogs`. See the [CLI API](provium-cli.md) for extending groups.
