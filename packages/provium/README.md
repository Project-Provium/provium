# Provium

[![Tests](https://github.com/Project-Provium/provium/actions/workflows/test.yml/badge.svg)](https://github.com/Project-Provium/provium/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/provium.svg)](https://pypi.org/project/provium/)

Provium records how typed artifacts were produced, including their procedure,
input values, artifact dependencies, and execution identity.

Requires Python 3.12 or newer:

```bash
python -m pip install provium
# Optional S3 access:
python -m pip install 'provium[s3]'
```

## Typed procedure inputs

Declare inputs on a `ProcedureInputs` class inside a procedure contract:

```python
from provium import (
    FilePath,
    ProcedureContract,
    ProcedureInputs,
    input_field,
    multi_input_field,
)

class ImportContract(ProcedureContract[None]):
    class Inputs(ProcedureInputs):
        source = input_field(FilePath)
        skip_rows = input_field(int)
        scale = input_field(float, optional=True)
        extra_sources = multi_input_field(
            FilePath, optional=True, minimum=2, maximum=5,
        )
```

Procedure callbacks receive `FilePath`, `int`, `float | None`, and
`tuple[FilePath, ...]`, with those types preserved for editors and type checkers.
Artifact definitions also work with both input helpers and yield typed read
bindings. Outputs use `output(ARTIFACT_DEFINITION)` or `optional_output(...)`.

- Single fields require one value unless `optional=True`.
- Multi-value fields default to at least one value and no upper bound.
- `optional=True, minimum=2` accepts zero values or at least two values.
- Integer inputs reject floats and booleans; floats accept finite numbers.
- `FilePath` accepts bare local paths, `file://` URLs, and `s3://bucket/key`.
  Construction performs no file access.
- `register_input_type` supplies typed decoding and JSON encoding for custom types.

Use `ProcedureExecutor.execute()` for one invocation or `prepare()` for reusable
setup state. Value inputs are snapshotted before callbacks and retained in output
provenance without becoming artifact dependency edges. The existing artifact
helpers `input`, `optional_input`, and `repeated_input` remain supported.

These value and multi-value features are currently for direct core execution.
Pipelines and dispatch accept single artifact fields, optionally absent.

## Guides and reference

- [Getting started](https://project-provium.github.io/provium/getting-started/)
- [Artifacts](https://project-provium.github.io/provium/guides/artifacts/)
- [Procedure implementations, execution, and custom codecs](https://project-provium.github.io/provium/guides/procedures/)
- [Command-line tools](https://project-provium.github.io/provium/guides/cli/)
- [Core API reference](https://project-provium.github.io/provium/reference/provium/)

For dynamic artifact workflows without a declared contract, use
`ImperativeProcedure`. For shared settings, subclass `ProcedureConfig`.
Application configuration and procedure inputs are separate APIs.

## Development

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
make check
make docs
```

Core checks include Ruff, Pyright, and 100% statement and branch coverage.
See the repository README for pipeline and dispatch integration tests.

## License

[MIT](LICENSE).
