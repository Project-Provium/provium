# Command-line tools

The command-line interface is included with `provium`. Both entry points use the
same plugin-discovered command catalog:

```bash
provium --version
python -m provium --version
```

## Application configuration

Every command supports repeatable `--config KEY.PATH=VALUE` overrides, including
commands registered by plugins and commands inside nested groups:

```bash
provium pipeline run -f pipeline.yaml --config pipeline.store=./artifacts
```

User defaults in `~/.config/provium/*.toml` are overridden by project
`.provium/*.toml` files, then environment variables such as
`PROVIUM_CONFIG__PIPELINE__STORE`, then `--config` values. Dedicated flags such
as `--store` take priority over these settings. See [Configuration](configuration.md)
for full precedence and the Python API. Procedure JSON/YAML files use the separate
`--procedure-config FILE` option.

## Discover procedures

List lightweight definitions without importing procedure implementations:

```bash
provium execute -l
```

Show a definition, its configuration schema, typed ports, cardinalities, and a
generated invocation synopsis:

```bash
provium execute example.DetectV1 --help
```

## Execute a procedure

Bindings use `FIELD=PATH`. Repeat `--input` for a repeated field and repeat
`--procedure-config` to apply JSON or YAML layers in order (later values override earlier
ones).

```bash
provium execute example.DetectV1 \
  --procedure-config defaults.yaml \
  --procedure-config production.json \
  --setup-input model=model.pa \
  --input image=image.pa \
  --input references=reference-1.pa \
  --input references=reference-2.pa \
  --output detections=detections.pa
```

Optional inputs and outputs may be omitted. Unknown fields, bad cardinality,
invalid configuration, resolution failures, and processing failures are printed
to standard error and return exit status 2. Successful execution prints its
provenance execution identity and returns 0.

Run `provium --help` or `provium execute --help` for the installed command
surface.

## Graph provenance

Render an artifact's lineage or generate DOT and Mermaid source with
`provium graph`. See [Provenance graphs](graphing.md) for backend setup, label
options, output behavior, and Python examples.

## Run pipelines

Install `provium-pipeline` to add the `provium pipeline` command group.
Use `provium pipeline --help` for commands to validate, inspect, run, register,
and manage pipeline queues. PostgreSQL and S3 support are optional extras.
See [Pipelines](pipelines.md) for installation, graph configuration, and examples.

## Enable tab completion

Provium completes commands, options, discovered procedure and artifact
identifiers, procedure binding fields, and filesystem paths inside bindings.

Enable completion for Bash or Zsh in the current shell:

```bash
eval "$(register-python-argcomplete provium)"
```

Add that line to `~/.bashrc` or `~/.zshrc` to enable it in future shells. You can
instead activate argcomplete globally for Python applications:

```bash
activate-global-python-argcomplete --user
```
