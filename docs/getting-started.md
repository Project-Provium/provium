# Getting started

Provium requires Python 3.12 or newer.

## Installation

```bash
python -m pip install provium
```

`provium` provides typed artifacts, procedure execution, sessions, provenance,
the `provium` command, and the command plugin API.

## Build a plugin

A plugin normally contains lightweight artifact and procedure definitions,
their concrete implementation classes, and catalogs published through package
entry points.

```toml
[project.entry-points."provium.artifact_catalogs"]
example = "example_plugin.catalogs:artifacts"

[project.entry-points."provium.procedure_catalogs"]
example = "example_plugin.catalogs:procedures"
```

Definitions make identifiers, descriptions, configuration schemas, and typed
ports inspectable without importing implementation modules. Resolution validates
the concrete classes only when execution or explicit inspection needs them.

After installing the plugin, verify discovery and inspect its contract:

```bash
provium execute -l
provium execute example.ProcessV1 --help
```

Execute it by binding the fields declared by its contract:

```bash
provium execute example.ProcessV1 \
  --procedure-config settings.yaml \
  --input source=source.pa \
  --output result=result.pa
```

Provium validates configuration and cardinality, streams output bodies to disk,
publishes outputs transactionally, and embeds their complete provenance lineage.

## Next steps

- Define [artifact classes and catalogs](guides/artifacts.md).
- Define and run [typed procedures](guides/procedures.md).
- Use the [command-line tools](guides/cli.md).
- Connect procedures into [pipelines](guides/pipelines.md).
