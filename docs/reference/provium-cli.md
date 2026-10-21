# Provium CLI API reference

The public API exported by `provium.cli`.

All registered commands and groups receive the repeatable `--config KEY.PATH=VALUE`
option from the core parser. Command implementations obtain merged settings lazily
with `self.configuration(arguments)`; shared helpers can use
`command_configuration(arguments)`. Reserve `--config` for application overrides
when declaring custom command options. See [Configuration](../guides/configuration.md)
for environment variables, value types, and precedence.

## Configuration helpers

::: provium.cli.configuration.command_configuration

::: provium.cli.configuration.configure_configuration

## CLI exports

::: provium.cli
    options:
      members: true
