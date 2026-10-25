# Provium Dispatch

[![Tests](https://github.com/Project-Provium/provium/actions/workflows/test.yml/badge.svg)](https://github.com/Project-Provium/provium/actions/workflows/test.yml)

[![PyPI](https://img.shields.io/pypi/v/provium-dispatch.svg)](https://pypi.org/project/provium-dispatch/)
[![Python](https://img.shields.io/pypi/pyversions/provium-dispatch.svg)](https://pypi.org/project/provium-dispatch/)
[![License](https://img.shields.io/pypi/l/provium-dispatch.svg)](https://pypi.org/project/provium-dispatch/)

[Documentation](https://provium.dlii.tech/guides/dispatch/) ·
[PyPI](https://pypi.org/project/provium-dispatch/) ·
[Issues](https://github.com/Project-Provium/provium/issues)

Durable orchestration for Provium pipelines: registered graphs, PostgreSQL queues
and computation leases, compute profiles, named input sets, and coordinated cache
maintenance.

## Installation

Requires Python 3.12 or newer. Install into your active Python environment:

```bash
python -m pip install provium-dispatch
```

This also installs Provium Pipeline, Provium, and the CLI. The base installation
supports local input-set indexing. For durable PostgreSQL queues, install the
database driver extra:

```bash
python -m pip install 'provium-dispatch[postgres]'
```

For PostgreSQL queues with S3 artifact storage, combine the extras:

```bash
python -m pip install 'provium-dispatch[postgres,s3]'
```

The PostgreSQL extra installs a client driver, not a database server. Provide a
running PostgreSQL database and its connection URL. Install your artifact and
procedure packages on every executor.

## Quick start

```bash
export PROVIUM_CONFIG__DISPATCH__DATABASE=postgresql://localhost/provium
provium dispatch register -f pipeline.yaml
provium dispatch enqueue text-processing \
  --input document=document.pa --output word_count=word-count.pa
provium dispatch run text-processing -v
provium dispatch queue text-processing
```

Configure `dispatch.store`, `dispatch.database`, and
`dispatch.scratch_directory` in `.provium/config.toml`, or use CLI flags.
Dispatch does not read `pipeline.*` defaults. Executors need the same artifact
store and installed procedure packages. PostgreSQL stores metadata; artifact
bytes remain in the cache.

For local input-set names, the base installation needs no database:

```bash
provium dispatch input-set register my-document --input document=document.pa
provium dispatch input-set resolve my-document
provium dispatch input-set find --input document=document.pa
provium dispatch lookup text-processing.outputs.word_count \
  -f pipeline.yaml --input-set my-document
provium dispatch input-set unregister my-document
```

Input-set hashes depend on logical bindings and fingerprints, independent of
pipeline, run, file paths, and UUIDs. Local indexes use SQLite with one connection
per operation; PostgreSQL supplies the index when configured.

Queue groups use `--queue/-q`. Output destinations are captured at enqueue.
Both `--strategy item` and `--strategy stage` are supported. Cache cleanup
currently supports local file stores and conservatively blocks when queued or
active work may need retained artifacts.

- [Dispatch guide and migration](../../docs/guides/dispatch.md)
- [Dispatch API reference](../../docs/reference/provium-dispatch.md)
- [Pipeline guide](../../docs/guides/pipelines.md)
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

Provium Dispatch is available under the [MIT License](https://github.com/Project-Provium/provium/blob/main/LICENSE).
