# Provium Pipeline

[![Tests](https://github.com/Project-Provium/provium/actions/workflows/test.yml/badge.svg)](https://github.com/Project-Provium/provium/actions/workflows/test.yml)

[![PyPI](https://img.shields.io/pypi/v/provium-pipeline.svg)](https://pypi.org/project/provium-pipeline/)
[![Python](https://img.shields.io/pypi/pyversions/provium-pipeline.svg)](https://pypi.org/project/provium-pipeline/)
[![License](https://img.shields.io/pypi/l/provium-pipeline.svg)](https://pypi.org/project/provium-pipeline/)

[Documentation](https://provium.dlii.tech/guides/pipelines/) ·
[PyPI](https://pypi.org/project/provium-pipeline/) ·
[Issues](https://github.com/Project-Provium/provium/issues)

Execute typed procedure graphs, reuse cached computations, and publish requested
artifacts. Supports per-item or per-stage scheduling, CSV batches, local/S3
storage, and custom cache, publisher, and coordinator implementations.

## Installation

Requires Python 3.12 or newer. Install into your active Python environment:

```bash
python -m pip install provium-pipeline
```

This also installs Provium and its CLI. For S3 artifact storage:

```bash
python -m pip install 'provium-pipeline[s3]'
```

Install the artifact and procedure packages referenced by your graph as well.
Basic execution needs neither PostgreSQL nor a compute profile.

## Quick start

```bash
provium pipeline run -f pipeline.yaml \
  --input document=document.pa --output word_count=word-count.pa -v
```

Direct CLI execution requires an output destination or a CSV with per-row
destinations. Outputs are cached under `./.provium/artifacts` by default and
copied to their destinations. Cached intermediate bodies are read when needed;
`--verify-artifacts` requests eager verification.

```bash
provium pipeline run -f pipeline.yaml --csv requests.csv --strategy stage
provium pipeline lookup text-processing.outputs.word_count \
  -f pipeline.yaml --input document=document.pa
provium pipeline cache clear --older-than 86400
```

CSV input headers use `inputs.NAME`; output headers use a target name or
`nodes.NODE.PORT`. Output cells contain paths; blank cells omit publication.

Basic pipelines do not require profiles or a database. Use
[Provium Dispatch](../provium-dispatch/README.md) for registered pipelines,
durable queues, profiles, and named input sets.

- [Pipeline guide](../../docs/guides/pipelines.md)
- [Pipeline API reference](../../docs/reference/provium-pipeline.md)
- [Extended text example](../../examples/provium-text-pipeline/README.md)
- [Configuration](../../docs/guides/configuration.md)

## Development

From the repository root, install the workspace test dependencies:

```bash
python -m pip install -e 'packages/provium[test,docs]' \
  -e 'packages/provium-pipeline[test]' -e 'packages/provium-dispatch[test]' \
  -e 'examples/provium-text-pipeline[test]'
```

Run the combined pipeline/dispatch suite with Docker running:

```bash
cd packages/provium-dispatch
../../.venv/bin/python -m pytest test ../provium-pipeline/test \
  --cov=provium_pipeline --cov=provium_dispatch --cov-branch --cov-fail-under=100
```

Testcontainers starts real PostgreSQL (`postgres:17-alpine`) and LocalStack S3
(`localstack/localstack:4`) on dynamic ports and tears them down automatically.
No manually started services or fixed test ports are required.

Run Ruff, Ruff formatting checks, and Pyright in each package directory.
Build the shared documentation from the repository root with `make docs`.

## License

Provium Pipeline is available under the [MIT License](https://github.com/Project-Provium/provium/blob/main/LICENSE).
