# Procedures and provenance

Procedures are discoverable, typed classes. A lightweight contract describes
configuration and typed input/output fields without importing the implementation; a
`ProcedureDefinition` connects that contract to its plugin class.

## Define a contract

```python
from provium import (
    ProcedureConfig,
    ProcedureContract,
    ProcedureInputs,
    ProcedureOutputs,
    input_field,
    multi_input_field,
    output,
)


class DetectionConfig(ProcedureConfig):
    threshold: float = 0.5


class DetectContract(ProcedureContract[DetectionConfig]):
    configuration = DetectionConfig

    class SetupInputs(ProcedureInputs):
        model = input_field(MODEL)

    class Inputs(ProcedureInputs):
        image = input_field(IMAGE)
        previous = input_field(DETECTIONS, optional=True)
        references = multi_input_field(IMAGE)

    class Outputs(ProcedureOutputs):
        detections = output(DETECTIONS)
```

Fields infer their reader or writer binding type from the artifact definition.
Input/output records are immutable. Optional single inputs become `None`;
multi-value inputs become ordered tuples. `multi_input_field` defaults to
`minimum=1, maximum=None`. Its minimum must be an integer of at least one.
With `optional=True, minimum=2`, zero values are allowed, but a nonempty
collection must contain at least two values. A maximum limits nonempty collections.
The legacy `input`, `optional_input`, and `repeated_input` artifact helpers
remain supported.

## Value inputs and import procedures

Both input helpers accept `int`, `float`, `FilePath`, and registered custom
types alongside artifact definitions. Setup inputs support the same types.

```python
from provium import FilePath, ProcedureInputs, input_field, multi_input_field

class ImportInputs(ProcedureInputs):
    source = input_field(FilePath)
    skip_rows = input_field(int)
    scale = input_field(float, optional=True)
    extra_sources = multi_input_field(FilePath, optional=True, minimum=2)
```

Editors and type checkers infer `FilePath`, `int`, `float | None`, and
`tuple[FilePath, ...]` respectively. Python input mappings accept typed values;
path fields also accept strings and path-like objects. Integer fields reject
floats (including `3.0`) and booleans. Float fields accept integers and finite
floats, excluding booleans. Numeric strings are parsed only at external boundaries
such as the CLI:

```console
provium execute example.ImportV1 --input source=s3://bucket/data.csv --input skip_rows=3
```

Supply a multi-value field by repeating its flag, for example
`--input extra_sources=a.csv --input extra_sources=b.csv`.

`FilePath` accepts bare local paths, `file://` URLs, and `s3://bucket/key`
locations without accessing them. It implements `os.PathLike[str]` and works
with `provium.files.local_file`. Relative paths resolve against the execution
process's working directory. A recorded location does not freeze the file's
contents; retain a checksum or immutable source version when that is required.

Register custom codecs before declaring their fields:

```python
from enum import Enum
from provium import register_input_type

class ImportFormat(Enum):
    CSV = "csv"
    TSV = "tsv"

register_input_type(
    ImportFormat,
    name="example.import-format.v1",
    decode=ImportFormat,
    encode=lambda value: value.value,
)

class FormatInputs(ProcedureInputs):
    format = input_field(ImportFormat)
```

Python calls supply an `ImportFormat` instance. External text uses the decoder.
Encoders return JSON-compatible values; names must be unique and should change
when the codec's meaning changes. Registration does not replace existing codecs.

Encoded values are snapshotted before callbacks run, available through
`result.value_inputs`, and saved in output artifacts' execution provenance.
They affect semantic fingerprints but do not create artifact dependency edges.

These value and multi-value features are currently for direct core execution.
Pipelines (including dispatch) accept only single artifact fields, optionally
absent; unsupported contracts fail during planning.

## Implement and register a procedure

```python
from provium import Procedure, ProcedureDefinition

DETECT = ProcedureDefinition(
    "example.DetectV1",
    "example_plugin.procedures:Detect",
    "Detect objects",
    "Run a model over an image.",
    "example_plugin.contracts:DetectContract",
)


class Detect(
    Procedure[
        DetectionConfig,
        DetectContract.SetupInputs,
        DetectContract.Inputs,
        DetectContract.Outputs,
    ]
):
    definition = DETECT

    def setup(self, context, configuration, inputs):
        with inputs.model.open() as reader:
            self.model = reader.read()

    def process(self, context, configuration, inputs, outputs):
        context.cancellation.raise_if_cancelled()
        with inputs.image.open() as reader:
            image = reader.read()
        outputs.detections.open().write(run_model(self.model, image))
```

Publish a `ProcedureCatalog` through the `provium.procedure_catalogs` entry-point
group. Discovery loads the catalog but does not import the definition target
until resolution or execution is requested.

## Execute directly

`ProcedureExecutor.execute()` validates layered configuration and bindings,
runs setup and processing once, finalizes all outputs transactionally, records
lineage, and closes the implementation.

```python
from provium import ProcedureExecutor

result = ProcedureExecutor().execute(
    DETECT,
    configuration_layers=({"threshold": 0.7},),
    setup_inputs={"model": ModelArtifact.bind_read("model.pa")},
    inputs={
        "image": ImageArtifact.bind_read("image.pa"),
        "references": [ImageArtifact.bind_read("reference.pa")],
    },
    outputs={
        "detections": DetectionsArtifact.bind_write("detections.pa"),
    },
)
```

Use `prepare()` when setup state should be reused. A prepared instance executes
sequentially, rejects concurrent reentry, and owns setup resources until
`close()`.

## Execution services

Setup and processing contexts expose a temporary directory and a thread-safe
`CancellationToken`. The setup directory lasts until the prepared procedure is
closed. Each processing directory is fresh and removed after that invocation.
Cancellation is cooperative: Provium checks immediately before user processing,
and long-running procedures should check at useful interruption points.

Declarative `setup()`, `process()`, and `close()` callbacks may access artifacts
only through the bound input and output values supplied to them. Creating new
bindings or calling an artifact class's `open()` or `create()` method inside a
declarative callback raises `RuntimeError`. Use `ImperativeProcedure` when the
set of artifact paths must be assembled dynamically.

## Imperative compatibility

For dynamic workflows that cannot use a declared callback, use the separate
`ImperativeProcedure` facade. Explicit bindings retain transactional writes and
provenance without overloading the typed `Procedure` base class.

```python
from provium import ImperativeProcedure

source = ImageArtifact.bind_read("source.pa")
destination = ImageArtifact.bind_write("copy.pa")
execution = ImperativeProcedure("example.CopyV1", "contract-digest").execute(
    inputs=(source,),
    outputs={"copy": destination},
)
with execution:
    with source.open() as reader:
        destination.open().write(reader.read())

print(execution.result.identity)
```

Migration note: the former `Procedure("name", "version")` value object is no
longer the public procedure type. Subclass `Procedure[...]` for declared plugins,
or use `ImperativeProcedure` for direct context-managed work.
