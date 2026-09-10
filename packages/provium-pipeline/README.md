# provium-pipeline

Simple YAML-defined, one-off pipelines for Provium. This is a separate distribution
and imports as `provium_pipeline`; it installs a `provium pipeline` CLI plugin.

From this repository:

```bash
python -m pip install -e packages/provium -e packages/provium-pipeline
```

Install any procedure plugins referenced by your pipeline as well.

## Definition

Procedure identifiers below are illustrative; substitute your installed procedures.
Input and output port names must match each procedure's contract.

```yaml
version: 1
name: summarize-documents
configuration:
  min_length: 100
  max_words: 200
inputs:
  - documents
steps:
  clean:
    procedure: example.clean_documents
    configuration:
      min_length: $config.min_length
    inputs:
      documents: $inputs.documents
  summarize:
    procedure: example.summarize_documents
    configuration:
      max_words: $config.max_words
    inputs:
      documents: $steps.clean.outputs.documents
outputs:
  summary: $steps.summarize.outputs.summary
  cleaning_report: $steps.clean.outputs.report
```

- `$inputs.name` refers to a single caller-supplied artifact. Its type is inferred
  from the connected procedure contracts. Shared inputs must have compatible types.
- `$steps.step_id.outputs.port` refers to a procedure's named output. No output
  declarations are needed on individual steps. All declared step outputs receive
  bindings; unexposed outputs use temporary storage.
- `$config.key` substitutes a configuration value while preserving its type.
  Nested keys such as `$config.model.temperature` are supported. References are
  entire values, not string interpolation. Use `$$` for a literal leading `$`.
- A step's `inputs` and `setup_inputs` map procedure ports to references. A repeated
  input takes a list of references, preserving order. Optional inputs may be omitted.
- Pipeline `outputs` rename step outputs for the caller. Every exposed output needs
  a destination; expose a port only once. To retain a specific intermediate, expose
  it here as another pipeline output.

For example, multiple artifacts on one repeated input:

```yaml
inputs:
  documents:
    - $steps.clean_a.outputs.documents
    - $steps.clean_b.outputs.documents
```

The planner validates configuration, references, required ports, cardinality,
artifact compatibility, and cycles before execution. Steps execute sequentially in
dependency order, regardless of their order in YAML. Every step runs, including
steps whose outputs are not exposed. Unknown definition keys are rejected.

## CLI

```bash
provium pipeline validate pipeline.yaml
provium pipeline run pipeline.yaml \
  --input documents=documents.pa \
  --output summary=summary.pa \
  --output cleaning_report=report.pa \
  --config max_words=150
```

`--config NAME=VALUE` accepts JSON values or plain strings. Repeat it for different
keys. `--config-file configuration.yaml` (or `.json`) supplies configuration layers;
files apply in argument order, after YAML defaults, followed by `--config` overrides.
Use configuration files to override nested mappings.

Add `--keep-intermediates ./debug` to retain artifacts in a unique run directory
under `./debug`. The CLI prints that directory after successful execution.

## Python

```python
from provium_pipeline import Pipeline, PipelineExecutor

pipeline = Pipeline.from_yaml("pipeline.yaml")
executor = PipelineExecutor()
plan = executor.plan(pipeline)  # Validate without running procedure callbacks.

# Documents, Summary, and Report are your plugin's artifact classes.
result = executor.execute(
    pipeline,
    configuration_layers=[{"max_words": 150}],
    inputs={"documents": Documents.bind_read("documents.pa")},
    outputs={
        "summary": Summary.bind_write("summary.pa"),
        "cleaning_report": Report.bind_write("report.pa"),
    },
)
```

`Pipeline.from_mapping(...)` accepts the same structure as YAML. By default the
executor discovers installed procedure catalogs; library callers can instead pass
`PipelineExecutor(procedures={definition.identifier: definition, ...})`.

`execute()` also accepts a Provium cancellation token and
`keep_intermediates="./debug"`. Its result contains a run identity, the public
artifact references in `outputs`, ordinary procedure execution results in `steps`,
and `intermediate_directory` (only set for retained runs). Normal Provium artifact
provenance records the actual producing procedures and their input lineage.

## Lifetime and scope

Each execution has fresh procedure instances and an isolated intermediate directory.
Temporary artifacts are removed on success, failure, and cancellation. Retained runs
are kept even when they fail; their directories remain under the requested root.
External inputs and explicit output destinations are never part of temporary cleanup.
Output destinations cannot overlap each other or external input paths.

Publication is transactional **per procedure**, not across the entire pipeline.
A public output committed by an earlier step remains if a later step fails. Exceptions
include a note naming the pipeline and failing step.

The definition, storage-independent plan, and execution lifetime are separate so
persistent runs, idempotency, and reusable artifacts can be added later. This release
has no caching, resume, persistence database, retries, parallel execution, nested
pipelines, or pipeline-wide rollback. Pipeline inputs each bind one artifact; lists
are supported on repeated procedure input ports.

## Development

From the repository root, with the packages and test dependencies installed:

```bash
python -m pytest -c packages/provium-pipeline/pyproject.toml packages/provium-pipeline/test
ruff check packages/provium-pipeline
pyright --project packages/provium-pipeline/pyproject.toml
```

Integration tests reuse the core package's test fixtures from the repository; those
fixtures are not runtime dependencies and are not shipped in the wheel.
