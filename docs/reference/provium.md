# Provium core API reference

The public API exported by `provium`.

Application settings have a separate, explicitly imported API. See
[Configuration](../guides/configuration.md) for file discovery and override precedence.

## Application configuration

::: provium.configuration.discover_configuration

::: provium.configuration.load_configuration

::: provium.configuration.parse_override

## Procedure input declarations

| Declaration | Instance value |
| --- | --- |
| `input_field(TYPE)` | One typed value (or artifact read binding). |
| `input_field(TYPE, optional=True)` | The typed value or `None`. |
| `multi_input_field(TYPE)` | An ordered tuple containing at least one value. |
| `multi_input_field(TYPE, optional=True, minimum=2, maximum=5)` | An empty tuple or two through five values. |

`TYPE` may be an artifact definition, `int`, `float`, `FilePath`, or a
custom Python type registered through `register_input_type`. Multi-value minimums
must be at least one; the default maximum is unbounded. Optionality is independent
of nonempty bounds. Python numeric inputs are validated strictly, while CLI text
is decoded. See the [procedure guide](../guides/procedures.md) for examples,
serialization, and pipeline restrictions.

`ProcedureIOFieldMetadata.value_type` identifies a value codec; its
`artifact_identifier` and `artifact_description` are `None` for value fields.
Use `required` or `optional` to inspect optionality instead of deriving it solely
from `minimum`.

`ProcedureExecutionResult.value_inputs` and
`ProcedureExecutionRecord.value_inputs` return isolated encoded snapshots keyed
by `setup.FIELD` or `inputs.FIELD`. Each entry contains `type` (the codec name)
and `value` (JSON data). Output artifact headers retain these snapshots separately
from artifact bindings, and semantic fingerprints include them. Existing artifact
records without value inputs retain their serialized form.

## Core exports

::: provium
    options:
      members: true
