# Configuration

Provium reads application settings from TOML files in two locations:

1. User defaults: `~/.config/provium/*.toml`.
2. Project overrides: `.provium/*.toml` in the current directory or its nearest ancestor containing a `.provium` directory.

Files in each directory are loaded in filename order. Project files override user files.
Tables merge recursively; other values, including arrays, are replaced. Missing directories
are fine. An empty `.provium` directory still marks a project boundary, so settings from
enclosing projects do not leak into nested projects.

Only immediate TOML files are read. Subdirectories such as `.provium/artifacts` are
not scanned. No directories or files are created automatically.

## Pipeline and dispatch CLI defaults

For example, create `~/.config/provium/config.toml`:

```toml
[pipeline]
store = "/data/provium/artifacts"
scratch_directory = "/tmp/provium"

[dispatch]
database = "postgresql://localhost/provium"
store = "/data/provium/artifacts"
scratch_directory = "/tmp/provium"
```

Override the store for a project in `.provium/config.toml`:

```toml
[pipeline]
store = "./.provium/artifacts"
```

Each command group reads its own section: `pipeline.*` for basic execution and
`dispatch.*` for durable execution and input-set indexing. Dispatch does not fall
back to pipeline settings. Python execution APIs take explicit arguments and do
not load these defaults automatically.

Commands resolve settings in this order, highest priority first:

1. Explicit CLI options: `--database`, `--store`, `--scratch-directory`.
2. Explicit `--config KEY.PATH=VALUE` overrides.
3. Generic `PROVIUM_CONFIG__...` environment variables.
4. Legacy environment aliases: `PROVIUM_PIPELINE_STORE` supplies either group's store; `PROVIUM_DATABASE_URL` supplies dispatch's database.
5. Project configuration.
6. User configuration.
7. Built-in defaults: the store is `./.provium/artifacts`; database and scratch directory are unset.

Store, database, and scratch-directory settings must be strings. The basic
pipeline group has no database setting. Relative paths are interpreted relative to the
working directory, just like CLI options, not relative to the TOML file.
Other settings are reserved for package consumers and do not become CLI options
automatically.

Configuration is loaded only when a command needs its settings, after argument parsing.
Imports, parser construction, help, and invalid CLI syntax do not read configuration.
Invalid TOML reports the source filename. Filesystem errors are reported rather than
silently ignored. Configuration does not import persistence drivers or storage backends.

Keep credentials out of committed project files; existing environment variables can
supply deployment-specific connection settings.

## Environment and command overrides

Any setting can be overridden through an environment variable prefixed with
`PROVIUM_CONFIG__`. Double underscores separate nested keys, and environment key
names are normalized to lowercase:

```bash
export PROVIUM_CONFIG__PIPELINE__STORE=/data/artifacts
export PROVIUM_CONFIG__PIPELINE__SCRATCH_DIRECTORY=/tmp/provium
provium pipeline inspect -f pipeline.yaml
```

Every command and command group accepts repeatable `--config KEY.PATH=VALUE` options:

```bash
provium pipeline inspect -f pipeline.yaml --config pipeline.store=./artifacts
provium --config pipeline.abc=default pipeline inspect -f pipeline.yaml --config pipeline.abc=xyz
```

Overrides accumulate across command groups in command-line order; the last value
for a key wins. A nested override replaces a scalar parent with a table.
Keys use letters, digits, underscores, and hyphens, starting with a letter or
underscore. Empty path components are rejected before command execution.

Generic environment and CLI values use JSON types when valid JSON, otherwise
they remain strings. For example, `pipeline.workers=3` is an integer,
`feature.enabled=true` is a boolean, and `--config 'pipeline.label="3"'`
keeps a numeric-looking value as a string. An empty value is an empty string.
Shell quoting is required for values containing spaces or JSON quotes.
Legacy environment variables retain their original string values.

These are application settings. The `execute` command's former
`--config FILE` option is now `--procedure-config FILE` for procedure-specific
JSON/YAML layers. This keeps `--config` consistent across all commands.

## Shared Python API

Packages can use the same discovery and merge rules without using the CLI:

```python
from provium.configuration import discover_configuration, load_configuration

files = discover_configuration()  # ordered tuple of pathlib.Path objects
settings = load_configuration()   # merged dict[str, object]
```

Both functions accept optional `user_directory` and `start_directory` arguments
as `pathlib.Path` objects. Use these to control discovery in applications and tests.
Each load reads a fresh snapshot; there is no process-global cache. Consumers validate
their own settings and decide when to load them. `load_configuration()` applies
generic environment variables after files and accepts an `overrides` iterable
of `KEY.PATH=VALUE` strings at highest priority. Pass `environment={}` to disable
environment overrides, or supply a mapping for a controlled environment.
Consumers can supply `environment_aliases={"section.key": "LEGACY_VARIABLE"}`;
these are applied below generic environment overrides.

Command classes inherit `self.configuration(arguments)`, which loads the same
settings with the invocation's accumulated `--config` overrides. Call it only when
execution needs settings, after the command's own validation. It performs no
automatic imports of consumer packages and does not turn arbitrary settings into
command-specific flags. Shared helpers can use
`provium.cli.configuration.command_configuration(arguments)` directly.

Application settings are separate from procedure configuration and pipeline graph
documents. Loading these files does not configure procedure inputs or execute Python
references.

## Dispatch queue defaults

Queued pipeline operations use `--queue NAME` / `-q NAME` when supplied.
Otherwise they resolve:

```toml
[dispatch.queue]
default_enabled = true
default_group = "default"
```

Set `default_enabled = false` to require explicit selection before persistence is
opened. Explicit names bypass default selection. Local execution remains available
without a queue. Both settings support the usual environment and `--config`
overrides, for example `PROVIUM_CONFIG__DISPATCH__QUEUE__DEFAULT_ENABLED=false`
or `--config dispatch.queue.default_group=priority`.
See [Named queues](dispatch.md#named-queues-and-retries) for operation and migration semantics.
