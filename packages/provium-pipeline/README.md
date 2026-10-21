# provium-pipeline

Provenance-addressed procedure graphs with local execution, PostgreSQL queues,
filesystem/S3 artifact storage, reusable process workers, and completion callbacks.

```sh
python -m pip install -e packages/provium -e packages/provium-pipeline
provium pipeline run -f pipeline.yaml --input document=/data/document.pa --store ./artifacts
```

The base installation supports local execution and filesystem storage. Install
optional backends as needed:

```sh
python -m pip install 'provium-pipeline[postgres]'
python -m pip install 'provium-pipeline[s3]'
python -m pip install 'provium-pipeline[postgres,s3]'
```

For an editable checkout, use `-e 'packages/provium-pipeline[postgres,s3]'`.
The `test` extra includes both drivers for the full development test suite.
Core Provium already provides its own optional `s3` extra.

Inputs must already be Provium artifacts. Install the procedure catalogs referenced
by the graph. All pipeline commands run through the core `provium` CLI.

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

The example procedure is provided by `examples/provium-text-pipeline`.
Compute profiles are separate YAML documents. Default execution uses one child
process; only one configured node is resident per context.

```sh
export PROVIUM_DATABASE_URL=postgresql://localhost/provium
provium pipeline register -f pipeline.yaml -f profiles.yaml
provium pipeline enqueue tokenize-documents --input document=/data/document.pa
provium pipeline run tokenize-documents --profile cluster --store s3://bucket/artifacts
provium pipeline status tokenize-documents --outputs
```

Repeated equivalent computations reuse verified results. PostgreSQL leases coordinate
shared computation across pipelines. Force replaces results through an atomic accepted
result reference while retaining immutable artifact versions for existing readers.

See the repository's [usage examples](../../docs/guides/pipelines.md) for
definitions, profiles, selectors, Python callbacks, time limits, and deployment.

## Application settings

Pipeline CLI defaults can be set in `~/.config/provium/*.toml` and overridden
by the nearest project `.provium/*.toml` directory:

```toml
[pipeline]
database = "postgresql://localhost/provium"
store = "./.provium/artifacts"
scratch_directory = "/tmp/provium"
```

Use `PROVIUM_CONFIG__PIPELINE__STORE=/data/artifacts` for an environment override,
or `provium pipeline run -f pipeline.yaml --config pipeline.store=./artifacts`
for an invocation override. Dedicated flags such as `--store` take highest priority.
Existing `PROVIUM_DATABASE_URL` and `PROVIUM_PIPELINE_STORE` variables remain supported.
See [Configuration](../../docs/guides/configuration.md) for discovery rules,
precedence, and typed overrides.

## Package organization

The CLI registers a native core `CommandGroup` under `pipeline`:

- `cli/commands/`: one class per command, each owning `configure` and `execute`.
- `cli/arguments.py`: shared option declarations and local argument validation.
- `documents.py`: YAML and local/remote document loading, imported only on use.
- `definitions.py`: CLI-independent definition discovery.
- `preparation.py`: graph and settings preparation with explicit inputs and injected persistence.

Commands dispatch through the core registry directly, without an action enum or
central execution switch. Each command imports its dependencies when needed.

Persistence is organized separately:
- `persistence/interface.py`: driver-independent queue and registry protocols.
- `persistence/backends/postgres.py`: PostgreSQL implementation and migrations.
- `persistence/factory.py`: explicit, lazy backend selection.

`ExecutionContext` accepts any implementation of `QueueBackend`.
`PersistenceBackend` extends that contract with registry and input management.
Use explicit implementation imports inside backend-specific code; public package
exports load on demand. Importing models, runtime, persistence contracts, or CLI
does not import PostgreSQL or S3 drivers. Help and invalid CLI arguments also
avoid loading pipeline runtime and worker code. Graph and settings validation precede
runtime loading; persisted-data validation requires the selected backend.
`validate` and `inspect` use planning directly without creating a store or loading
runtime and worker code.

Other packages can contribute actions to `pipeline` or any nested group by
registering groups with the same names in their own command catalogs. Core merges
those paths recursively while rejecting duplicate leaf commands. The standalone
`provium-pipeline` script and `python -m provium_pipeline` runner are removed;
use `provium pipeline ...` (or `python -m provium pipeline ...`).

```python
from provium_pipeline.persistence import Backend, open_backend

persistence = open_backend(connection_url, backend=Backend.Postgres)
```

PostgreSQL is the default and currently the only persistence implementation.
The existing `--database` option and `PROVIUM_DATABASE_URL` environment variable
remain supported. Persistence APIs live under `persistence`; the legacy
`database.py` facade and root persistence re-exports have been removed. Import
concrete backends explicitly from `provium_pipeline.persistence.backends.postgres`.

## Development

```sh
python -m pip install -e 'packages/provium[test]' -e 'packages/provium-pipeline[test]' \
  -e examples/provium-text-pipeline
python -m pytest packages/provium-pipeline/test
ruff check packages/provium-pipeline
ruff format --check packages/provium-pipeline
pyright --project packages/provium-pipeline/pyproject.toml
```

For PostgreSQL and S3 integration tests:

```sh
docker run -d --name provium-pipeline-postgres \
  -e POSTGRES_PASSWORD=provium -e POSTGRES_DB=provium \
  -p 127.0.0.1:55432:5432 postgres:17
docker run -d --name provium-pipeline-localstack \
  -e SERVICES=s3 -p 127.0.0.1:54566:4566 localstack/localstack:4

export PROVIUM_TEST_DATABASE=postgresql://postgres:provium@localhost:55432/provium
export PROVIUM_TEST_S3=1 AWS_ENDPOINT_URL_S3=http://localhost:54566
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1
python -m pytest packages/provium-pipeline/test
```

Integration tests create unique records and buckets. Use a disposable test database.
Slurm/AWS Batch deployment uses the same CLI with externally allocated resources;
those scheduler services are not required for local integration tests.
