# provium-pipeline

Provenance-addressed procedure graphs with local execution, PostgreSQL queues,
filesystem/S3 artifact storage, reusable process workers, and completion callbacks.

```sh
python -m pip install -e packages/provium -e packages/provium-pipeline
provium-pipeline run -f pipeline.yaml --input document=/data/document.pa --store ./artifacts
```

Inputs must already be Provium artifacts. Install the procedure catalogs referenced
by the graph. The same command is available as `provium pipeline`.

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
provium-pipeline register -f pipeline.yaml -f profiles.yaml
provium-pipeline enqueue tokenize-documents --input document=/data/document.pa
provium-pipeline run tokenize-documents --profile cluster --store s3://bucket/artifacts
provium-pipeline status tokenize-documents --outputs
```

Repeated equivalent computations reuse verified results. PostgreSQL leases coordinate
shared computation across pipelines. Force replaces results through an atomic accepted
result reference while retaining immutable artifact versions for existing readers.

See the repository's [usage examples](../../docs/pipeline-api-examples.md) for
definitions, profiles, selectors, Python callbacks, time limits, and deployment.

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
