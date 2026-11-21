# Cloud API reference

See [Cloud execution](../guides/cloud.md) for an executable workflow and
[Testing on AWS](../guides/cloud-testing.md) for disposable infrastructure.
`provium-cloud` depends on core and pipeline, with no dispatch dependency.

## Public package exports

Import `CloudClient`, `CloudDeployment`, `BackendConfiguration`, `JobSettings`,
`SchedulingOptions`, `SchedulingRule`, `load_deployment`, and `__version__` from
`provium_cloud`. `__version__` comes from the installed package metadata.

## Client and runs

`CloudClient(deployment, *, artifacts=None, state=None, executor=None)` opens the
configured backends unless instances are injected. Use it as a context manager;
it closes its owned artifact backend, while an injected backend remains the caller's
responsibility. State backends open/close their connections within transactions.

| Method | Purpose |
| --- | --- |
| `from_file(path)` | Load a deployment and open its backends. |
| `enqueue_pipeline(graph, inputs, *, request_id, targets, force, overrides)` | Admit an unnamed graph from a mapping, `PipelineGraph`, or file path. Optional arguments have defaults. |
| `enqueue_procedure(procedure, inputs, *, request_id, configuration, tags, force, overrides)` | Admit a standalone procedure; optional arguments have defaults. |
| `enqueue(plan, inputs, *, request_id=None)` | Admit an already prepared `InvocationPlan`. |
| `get_run(identity)` | Refresh and return durable run state. |
| `list_runs(*, statuses=None, input_set_id=None, cursor=None, limit=100)` | Return `RunPage(items, next_cursor)`; limit must be 1–999. |
| `cancel(identity)` / `retry(identity)` | Cancel demand or retry unfinished tasks of a failed/canceled run. |
| `attempts(identity, node)` | Return the task's shared computation history, or `None` before admission. |
| `wait(identity, *, timeout=None, interval=2)` | Poll until terminal; raise `TimeoutError` on a wait deadline. Does not schedule work. |
| `lookup(computation)` | Verify and return latest successful output references, or `None`. |
| `fetch(identity, output, destination, *, overwrite=False)` | Publish an available run output to a destination. |

`wait` returns a terminal run even if it failed or was canceled. Call
`Run.raise_for_failure()` to require success. Run status values are `queued`,
`running`, `succeeded`, `failed`, and `canceled`. Task status values are `waiting`,
`queued`, `running`, `completed`, `failed`, `blocked`, and `canceled`.

`Run.progress` computes counts from tasks, including `total` and completed `reused`
tasks. It is added to native CLI run JSON, but is not a serialized model field.
`input_set_id` hashes logical input bindings/identities; it is not a pipeline ID.

::: provium_cloud.client.CloudClient

::: provium_cloud.state.models.Run

::: provium_cloud.state.models.RunPage

::: provium_cloud.state.models.Task

::: provium_cloud.state.models.Computation

::: provium_cloud.state.models.Generation

## Deployment and planning

Deployment models reject unknown fields. `CloudDeployment.resolve` applies
defaults, matching rules in order, then explicit scheduling overrides. A deployment
has no `name`; it is supplied directly and retained with admitted work.
`load_deployment` reads one local YAML/JSON document, rejects duplicate/non-string
mapping keys, and validates its schema without connecting to services.

`prepare_pipeline` resolves a graph and selected targets into a serializable
`InvocationPlan`. `prepare_procedure` produces a single task named `procedure`.
These functions discover installed contracts but do not open state/artifact backends.
A task plan contains the procedure record/configuration, input/setup bindings,
expected output types, resolved job settings, and its force decision.

::: provium_cloud.deployment.config.CloudDeployment

::: provium_cloud.deployment.config.BackendConfiguration

::: provium_cloud.deployment.config.SchedulingOptions

::: provium_cloud.deployment.config.JobSettings

::: provium_cloud.deployment.config.SchedulingRule

::: provium_cloud.deployment.loader.load_deployment

::: provium_cloud.invocations.models.PipelineGraph

::: provium_cloud.invocations.models.InvocationPlan

::: provium_cloud.invocations.models.TaskPlan

::: provium_cloud.invocations.planning.prepare_pipeline

::: provium_cloud.invocations.planning.prepare_procedure

## Execution

`Controller.tick()` advances runnable DAG nodes, reuses verified outputs, and
reconciles submissions, leases, and cancellations. `serve(interval=2, stop=None)`
repeats this work and logs/retries transient exceptions. A positive interval is
required. The controller uses the injected executor when provided, otherwise an
AWS adapter for the admitted deployment's region.

The worker receives a computation key, generation number, and attempt token. It
claims ownership, checks the installed procedure record, materializes inputs,
executes in a child process, renews its lease, stages outputs, and commits only
while ownership is valid. Duplicate/stale claims return `False`; successful
execution returns `True`. Exceptions can record a failed generation and propagate.

`BatchExecutor` sets Batch retry attempts to one and reconciles ambiguous submissions
by attempt-derived job name. It accepts injected SDK clients for tests. There is
currently no provider entry-point registry; pass an `Executor` instance to the
client or controller for custom compute behavior. Deployment validation still
requires `provider: aws`.

::: provium_cloud.execution.controller.Controller

::: provium_cloud.execution.worker.execute_job

::: provium_cloud.execution.interface.Executor

::: provium_cloud.execution.aws.batch.BatchExecutor

## State backends

Built-in configurations:

| Backend | Options |
| --- | --- |
| `sqlite` | `path`: SQLite database file. |
| `postgres` | `connection_env`: name of an environment variable containing the connection string. Requires the `postgres` extra. |

State holds runs, request-id mappings, computation generations, leases, and committed
output references. It is partitioned by the artifact backend's namespace. SQLite
uses serialized write transactions; PostgreSQL uses an advisory transaction lock
per namespace and database time for lease decisions.

Custom state backends must implement serializable, atomic transactions, including
absent-key reads and request-id uniqueness. Roll back on exceptions. Keep artifact
and compute network I/O outside the state transaction. Preserve the ordered paging
and model semantics of `StateTransaction`, and use a coherent time source for leases.

Register a factory accepting `(options, *, namespace)` using an entry point:

```toml
[project.entry-points."provium.cloud.state_backends"]
custom = "your_package.state:create_backend"
```

Select `state.backend: custom` and supply its options in the deployment. Unknown or
ambiguous backend registrations are rejected.

::: provium_cloud.state.open_state

::: provium_cloud.state.interface.StateBackend

::: provium_cloud.state.interface.StateTransaction

## Artifact backends

The `file` and `s3` backends accept `location` and optional `scratch_directory`.
`file` requires a local path or `file://` location; `s3` requires `s3://`.
S3 requires the AWS SDK. These adapters reuse pipeline's artifact verifier and file
publisher, while cloud state remains the authority for committed results.

Artifact references are JSON-compatible dictionaries with `location`, `identity`,
and `fingerprint`. A backend must return stable namespace and fingerprint semantics,
materialize verified inputs, and stage immutable output objects matching the
expected provenance keys. `stage` does not publish authoritative completion;
the coordinator's fenced state commit does that. Rejected/uncommitted objects may
remain until a separate maintenance process removes them.

A custom artifact factory accepts `(options)`:

```toml
[project.entry-points."provium.cloud.artifact_backends"]
custom = "your_package.artifacts:create_backend"
```

Select it with `artifacts.backend: custom`. There is no built-in PostgreSQL
artifact-blob backend or cloud artifact garbage collector.

::: provium_cloud.artifacts.open_artifacts

::: provium_cloud.artifacts.interface.ArtifactBackend

## CLI reference

All commands below are under `provium cloud aws`. Ordinary management commands
require `--deployment/-d FILE`; `worker` can instead read
`PROVIUM_CLOUD_DEPLOYMENT` from its environment. Use each command's `--help` for
argument combinations.

| Command | Main arguments / result |
| --- | --- |
| `validate` | `-d FILE`; validates the deployment schema locally. |
| `plan` | `-f GRAPH` or `--procedure ID`; outputs the resolved plan. |
| `enqueue` | Same selection, repeatable `--input NAME=VALUE`, optional `--request-id`; returns a run. |
| `controller` | `--once`, `--interval`; schedules and reconciles work. |
| `worker` | `--computation`, `--generation`, `--attempt`; internal Batch execution entry point. |
| `list` | Repeatable `--status`, `--input-set-id`, `--cursor`, `--limit`; returns a run page. |
| `status` | `RUN_ID`; returns a refreshed run with progress. |
| `inspect` | `RUN_ID`, optional `--node NAME`; returns a run or computation history. |
| `cancel` / `retry` | `RUN_ID`; updates durable run state. |
| `wait` | `RUN_ID`, `--timeout`, `--interval`; nonzero on failure, cancellation, or wait timeout. |
| `lookup` | `COMPUTATION_KEY`; returns `{ "outputs": ... }`. |
| `fetch` | `RUN_ID OUTPUT DESTINATION`, optional `--overwrite`. |
| `logs` | `JOB_ID`, `--cursor`, `--limit` (1–10000); returns events and `next_cursor`. |

Planning/admission support direct `--job-queue`, `--job-definition`, and `--timeout`
overrides. `--force` applies to all selected tasks; repeatable `--force-selector`
applies to pipeline selectors. Pipeline `--target` is repeatable. Standalone
procedures accept `--parameter` and `--tag` instead of pipeline target/force selectors.
Management/planning commands accept `--deployment-set` and `--region`; the minimal
`validate` command only accepts its deployment file. Commands returning results emit
JSON with exit code 0; the controller logs its activity instead. Run failure through
`wait` returns 1; handled command errors
return the CLI's error code. These are cloud run IDs, not AWS job IDs.
