# Provium

[![Documentation](https://github.com/Project-Provium/provium/actions/workflows/docs.yml/badge.svg)](https://github.com/Project-Provium/provium/actions/workflows/docs.yml)
[![codecov](https://codecov.io/gh/Project-Provium/provium/graph/badge.svg)](https://codecov.io/gh/Project-Provium/provium)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/Project-Provium/provium/blob/main/LICENSE)

Provium is a provenance-first Python framework for building trustworthy,
reproducible data workflows. It records how artifacts were produced as part of
normal execution, connecting each result to the procedure, inputs, and execution
that created it.

Instead of treating lineage as separate documentation that can drift out of
date, Provium makes provenance part of the workflow itself. The result is an
auditable history that helps teams understand where data came from, reproduce
past work, and inspect dependencies with confidence.

[Read the documentation](https://project-provium.github.io/provium/) to get
started and explore the complete guides and API reference.

## What Provium provides

- **Provenance-aware artifacts** that retain their production history.
- **Typed procedures** for defining clear, reusable units of work.
- **Reproducible execution** with recorded inputs, outputs, and identities.
- **Lineage inspection and visualization** through Python and the command line,
  including Graphviz and Mermaid output.
- **Extensible tooling** through a typed library, CLI, and command plugin system.

## Repository layout

- [`packages/provium/`](packages/provium/) — the Provium library, command-line
  interface, and tests.
- [`packages/provium-pipeline/`](packages/provium-pipeline/) — graph execution,
  caching, CSV requests, and artifact publication.
- [`packages/provium-dispatch/`](packages/provium-dispatch/) — durable queues,
  profiles, registry, and input-set indexing.
- [`examples/`](examples/) — installable projects demonstrating Provium in use.
- [`docs/`](docs/) — guides, concepts, and reference documentation.

## Development

Create or activate the repository-level virtual environment, then install the core and example
packages in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
```

Run the core and example checks from the repository root:

```bash
make check
```

For pipeline and dispatch installation, Ruff/Pyright checks, and the combined
Testcontainers integration suite, see the
[pipeline development instructions](packages/provium-pipeline/README.md#development).
Docker must be running for PostgreSQL and LocalStack tests.

Build the shared documentation site into `site/`:

```bash
make docs
```

The Provium package owns its `pyproject.toml`, tests, coverage gate, version,
and build metadata; examples remain separate projects in the monorepo.

## Recover a failed PyPI publication

Release Please can create GitHub releases before a later build or publication
fails. A subsequent push does not republish those existing releases.

After fixing the workflow on `master`, open **Actions → Release → Run workflow**,
select `master`, and enter the existing release tags for the packages to publish.
Leave already-published packages blank. At least one tag is required; tags must
match the package name and have an existing, non-draft GitHub release.

For example, to recover the three releases created on October 25, 2026:

```bash
gh workflow run release.yml --repo Project-Provium/provium --ref master \
  -f provium_tag=provium-v0.12.1 \
  -f pipeline_tag=provium-pipeline-v2.2.0 \
  -f dispatch_tag=provium-dispatch-v0.2.0
```

The current workflow tests and builds source from the selected tags, then
publishes core, pipeline, and dispatch in dependency order through the existing
`pypi` environment and Trusted Publisher. Any required environment approvals
still apply. Ensure omitted dependencies are already available on PyPI.
Recovery does not create tags, change versions, or run Release Please.
A new manual run is required to use a workflow fix; rerunning an old failed run
uses its old workflow.
