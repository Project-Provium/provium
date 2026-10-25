# Dispatch API reference

Dispatch depends on pipeline and adds persistence. Pipeline does not depend on dispatch. See the [dispatch guide](../guides/dispatch.md) for installation, queue workflows, and migration.

## Models and preparation

`provium_dispatch.models.ComputeProfile` extends pipeline `ExecutionSettings` with `name` and document kind `ComputeProfile`. `provium_dispatch.models.PipelineDefinition` extends the basic input/output contract with `profiles: frozenset[str]`. An empty allowlist does not restrict profile selection.

`provium_dispatch.preparation.prepare_pipeline` accepts `files` for direct documents or `name` with an injected `backend` for registered execution. Other keyword arguments are `scratch_directory`, `definition_reference`, `profile_name`, `workers`, `only`, and `force`. The prepared object includes its plan, definition, selected profile, backend, overrides, and settings.

::: provium_dispatch.models.ComputeProfile

::: provium_dispatch.models.PipelineDefinition

::: provium_dispatch.preparation.prepare_pipeline

## Durable runtime

Import `ExecutionContext` from `provium_dispatch.runtime`. It adapts the basic executor to a `QueueBackend`, accepting `profile`, `backend`, and `queue_group="default"` in addition to common execution options such as strategy, verification, targets, and publisher.

Calling `start()` with a backend consumes queued work. Direct requests are supported through the same request types as pipeline. Callback and shutdown semantics match the basic runtime. Python calls do not resolve application configuration; provide database, store, and queue explicitly.

::: provium_dispatch.runtime.ExecutionContext

## Backend selection

```python
from provium_dispatch.persistence.factory import Backend, open_backend

backend = open_backend(
    "postgresql://localhost/provium",
    backend=Backend.Postgres,
)
```

Install `provium-dispatch[postgres]`. PostgreSQL is currently the only supplied durable backend. The selector is an enum member, not a string. Direct construction is available from `provium_dispatch.persistence.backends.postgres.PostgresBackend`; it is not a root-level package export.

Schema initialization/migrations are performed by the backend. Database storage contains registered documents, queued requests, accepted result metadata, leases, and input-set index records. Artifact bytes remain in the selected cache.

## Persistence contracts

Interfaces live in `provium_dispatch.persistence.interface`.

`QueueBackend` extends pipeline `Coordinator` and `WorkQueue` with `reader(namespace)`, a scoped read/maintenance guard, and `definition(pipeline)`, the registered application contract.

`PersistenceBackend` adds registry, admission, input-set indexing, and `MaintenanceGuard` operations:

| Method | Behavior |
| --- | --- |
| `register(documents, *, definitions=None)` | Register serialized graph/profile documents and optional contracts. |
| `registered(kind=None)` | List registered documents. |
| `get(kind, name)` | Read a registered document. |
| `unregister(documents)` | Remove selected registrations and return counts. |
| `enqueue(pipeline, inputs, *, queue_group="default", destinations=None)` | Admit a request and return its identity. |
| `pending(pipeline, after=0, limit=100, include_finished=False, *, queue_group="default")` | Page queued work in a selected group. |
| `progress(identity, state, detail)` | Update a queued request. |
| `queue_info(pipeline=None, *, queue_group="default")` | Summarize counts and states. |
| `retry(pipeline, *, queue_group="default")` | Reset failed work for another attempt. |

Queue summaries include pipeline, queue, total, and states. Input identities remain globally unique, while consumption and retries are scoped to the selected queue group.

Implementations must preserve admitted graph/input snapshots, deduplication, ownership fencing, and coordination between readers, admissions, and maintenance. Accepted results are shared by computation identity rather than queue group. Queued destinations are durable request data and participate in request deduplication.

::: provium_dispatch.persistence.interface.QueueBackend

::: provium_dispatch.persistence.interface.PersistenceBackend

## Input-set index

`provium_dispatch.input_sets.InputSetIndex` is implemented by `LocalInputSetIndex(root)` and the PostgreSQL backend.

| Method | Result |
| --- | --- |
| `register_input_set(name, inputs)` | Record containing `hash` and `inputs`. |
| `resolve_input_set(*, name=None, key=None)` | Indexed record selected by exactly one name or hash. |
| `find_input_sets(key)` | Sorted matching names. |
| `unregister_input_sets(*, name=None, key=None)` | Removed names; the hash record remains. |

Use `provium_pipeline.input_sets.inspect_inputs` to create verified records before registration. Hashes derive from logical input names and fingerprints. The local index opens a separate SQLite connection per operation and lives at `<store>/input-sets.sqlite3`; it is not a durable execution queue.

::: provium_dispatch.input_sets.InputSetIndex

## Cleanup contract and limits

The backend's `hold(namespace)` prevents unsafe concurrent maintenance; `forget(namespace, keys)` removes accepted metadata before cache bytes are deleted. The supplied PostgreSQL implementation conservatively protects active readers, unexpired running leases, and pending/partial/cancelled queue work. Protection is not yet narrowed to each artifact's exact queued consumers.

`FileCache` and byte/age filtering remain pipeline responsibilities. S3 deletion and a database-blob cache inventory are not supplied. Do not implement a custom cleaner that bypasses backend guards or treats a separate preflight query as sufficient concurrency control.

## CLI surface

| Command | Purpose |
| --- | --- |
| `dispatch register -f FILE` | Register graphs/profiles and contracts. |
| `dispatch unregister NAME` / `--profile NAME` / `-f FILE` | Remove selected registry entries. |
| `dispatch list [pipelines\|profiles]` | List registry contents. |
| `dispatch enqueue NAME --input KEY=PATH --output TARGET=PATH` | Admit inputs with destinations; `--csv` supports batches. |
| `dispatch run NAME` | Consume registered queued work. |
| `dispatch run -f FILE --input KEY=PATH --output TARGET=PATH` | Direct dispatch execution. |
| `dispatch validate`, `inspect`, `status` | Inspect registered or file-defined execution. |
| `dispatch queue [NAME]` | Show selected queue summaries. |
| `dispatch retry NAME` | Retry failed requests. |
| `dispatch input-set register NAME --input KEY=PATH` | Name verified input bindings. |
| `dispatch input-set resolve NAME` | Resolve a name to its record. |
| `dispatch input-set find --input KEY=PATH` | Find names for equivalent inputs. |
| `dispatch input-set unregister NAME` or `--input KEY=PATH` | Remove aliases. |
| `dispatch lookup REF --input-set NAME` | Locate a cached output; also accepts direct inputs or an indexed hash. |
| `dispatch cache clear` | Guarded local cache maintenance. |

Use `--queue/-q` on enqueue, registered run/status, queue, and retry. `--profile` selects compute settings. `--database`, `--store`, and `--scratch-directory` override `dispatch.*` configuration. `--json` is available for structured output. Consult command `--help` for exact combinations; registered execution takes inputs/destinations from the queue.
