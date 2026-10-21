# Pipeline API reference

Import domain APIs from their owning modules. PostgreSQL and S3 drivers are
optional and load only when selected. See the [pipeline guide](../guides/pipelines.md)
for installation and end-to-end examples.

## Models and documents

| Import | Purpose |
| --- | --- |
| `provium_pipeline.models.PipelineConfiguration` | Validated graph, declared inputs, nodes, and outputs. |
| `provium_pipeline.models.ComputeProfile` | Worker counts and time limits. |
| `provium_pipeline.models.PipelineDefinition` | Application input/output contract and allowed profiles. |
| `provium_pipeline.documents.load_documents` | Load local or S3 YAML into configuration/profile objects. |

`load_documents(paths, *, scratch_directory=None)` accepts multiple paths and
multi-document YAML. Model imports do not load YAML or file-handling modules;
the high-level `provium_pipeline.load_documents` export loads them on demand.

A definition can use artifact identifiers or artifact definitions:

```python
from provium_pipeline.models import PipelineDefinition

definition = PipelineDefinition(
    inputs={"document": "provium_text_pipeline_example.DocumentV1"},
    outputs={"tokens": "provium_text_pipeline_example.TokensV1"},
    profiles=frozenset({"local"}),
)
```

Select it using `--definition application:definition`, or publish an entry
point whose name matches the graph:

```toml
[project.entry-points."provium.pipeline.definitions"]
tokenize-documents = "application:definition"
```

`provium_pipeline.definitions.load_definition(name, reference)` resolves an
explicit module/attribute reference, or discovers the named entry point when
`reference` is `None`. Without a match it returns `None`; duplicate matches are
errors. A supplied definition must agree with the graph's declared input types
and restricts which named profiles can be selected.

## Preparation

`provium_pipeline.preparation.prepare_pipeline` accepts keyword arguments:

| Argument | Meaning |
| --- | --- |
| `files` | YAML paths for a local graph and profiles; defaults to an empty sequence. |
| `name` | Registered graph name, used with `backend` instead of files. |
| `backend` | Injected `PersistenceBackend`; preparation never opens a database itself. |
| `scratch_directory` | Temporary download directory. |
| `definition_reference` | Optional `module:attribute` reference. |
| `profile_name` | Profile to select from documents or persistence. |
| `workers` | List of selector/count mappings, such as `[{"nodes.tokenize": 2}]`. |
| `only`, `force` | Lists of node/tag selectors. |

The result is a `PreparedPipeline` containing `plan`, `definition`, `profile`,
`backend`, `overrides`, and resolved `settings`. Its `summary()` returns a
JSON-compatible mapping. Preparation validates contracts and settings without
starting workers or creating an artifact store.

`load_pipeline(*, files=(), name=None, backend=None, scratch_directory=None)`
selects a configuration without preparing its execution plan.
`provium_pipeline.planning.build_plan(configuration, definition=None,
procedures=None)` validates an already-loaded graph.

## Execution and artifacts

`provium_pipeline.runtime.ExecutionContext` takes a configuration and an
`ArtifactStore`. Optional parameters include `definition`, `procedures`,
`profile`, `workers`, `only`, `force`, `backend`, `idle_wait`, and `log_path`.
Pass prepared values explicitly; preparing a graph does not start execution.

`InputSet(inputs)` maps declared graph inputs to artifact locations or supported
artifact values. `start(input_sets=(), *, on_step_finished=None,
on_pipeline_finished=None)` starts once and returns immediately.
Context exit joins work; executor failures can raise at shutdown. Individual
input failures are represented by outcomes.

`Outcome` exposes `input_set`, `state`, `outputs`, `node`, `reused`, and
`error`. Read completed outcomes from `execution.outcomes`. Callback delivery
is best effort; callbacks should not be the sole record of persistent work.

`provium_pipeline.store.ArtifactStore(location, *, scratch=None)` manages
verified local/S3 artifacts. Use it as a context manager. An `Artifact` has
a durable `location`, a local `path`, an `identity`, and a `fingerprint`.
Downloaded S3 paths belong to the store and must be used before it closes.

## Persistence

Contracts live in `provium_pipeline.persistence.interface`:

- `QueueBackend` provides execution coordination, leases, and results.
- `PersistenceBackend` extends it with registry and queued-input management.
- `Lease` identifies a computation's current ownership generation.

`ExecutionContext` accepts any implementation of `QueueBackend`.
For the supplied backend, either select it through the factory:

```python
from provium_pipeline.persistence.factory import Backend, open_backend

backend = open_backend(
    "postgresql://localhost/provium", backend=Backend.Postgres
)
```

or import the implementation explicitly:

```python
from provium_pipeline.persistence.backends.postgres import PostgresBackend

backend = PostgresBackend("postgresql://localhost/provium")
```

Install `provium-pipeline[postgres]` first. PostgreSQL is currently the only
supplied persistence implementation. Backend selectors are enum members, not
strings. There is no `provium_pipeline.database` facade or root-level
`PostgresBackend` export.

## Application settings

The CLI consumes `pipeline.database`, `pipeline.store`, and
`pipeline.scratch_directory` from the shared core configuration system.
These defaults apply to CLI execution; Python APIs continue to accept explicit
arguments and do not automatically read user or project configuration.
See [Configuration](../guides/configuration.md) for files, environment overrides,
and the global `--config KEY.PATH=VALUE` option.

## CLI extensions

Pipeline commands are ordinary core `Command` classes with their own
`configure(parser)` and `execute(arguments)` methods. They are registered under
a `CommandGroup` named `pipeline`.

Other packages can contribute to this group, or arbitrarily nested groups,
through `provium.cli.command_catalogs` entry points. Matching group paths merge
across packages; duplicate leaf command names are errors. Keep constructors and
argument configuration lightweight and import execution dependencies after
argument validation. See the [CLI API reference](provium-cli.md).

## Queue selection API

Queue operations accept a keyword-only `queue_group: str = "default"`:

- `backend.enqueue(pipeline, inputs, queue_group="priority")`
- `backend.pending(pipeline, after=0, limit=100, include_finished=False, queue_group="priority")`
- `backend.retry(pipeline, queue_group="priority")`
- `backend.queue_info(pipeline=None, queue_group="priority")`

`queue_info` summaries include `pipeline`, `queue`, `total`, and `states`.
Pending rows include their `queue_group`. Input IDs remain globally unique, so
`progress` and lease admission continue to use the input ID.

Pass `queue_group="priority"` to `ExecutionContext` to consume that group when
using a backend. Python calls do not read CLI configuration; the omitted argument
always selects `default`. Custom persistence implementations must accept the new
keyword and restrict reads and retries to the selected group.
See [Named queues](../guides/pipelines.md#named-queues) for CLI and migration details.
