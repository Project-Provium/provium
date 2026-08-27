# Definitions and compilation

A definition is editable user intent. Compilation turns it into an immutable, deterministic execution contract. Keeping those stages separate lets users author friendly YAML or Python while workers consume a stable plan.

## Definition document

The schema is `provium.pipeline/v1`. A definition contains:

- pipeline identity, version, label, and description;
- named pipeline inputs;
- procedure nodes with setup bindings, processing bindings, configuration, cache policy, and metadata;
- named pipeline outputs that reference node output fields.

Definition models use Pydantic with unknown fields forbidden. YAML and JSON loaders report the source name and a best-effort field path when validation fails.

### Example

```yaml
schema: provium.pipeline/v1
pipeline:
  identifier: example.tokenize
  version: "1"
  label: Tokenize documents

inputs:
  document:
    artifact: provium.example.DocumentV1
    scope: record

nodes:
  tokenize:
    uses: provium.example.TokenizeV1
    inputs:
      document: $inputs.document
    config:
      lowercase: true

outputs:
  tokens: $nodes.tokenize.tokens
```

## Inputs and cardinality

A pipeline input has an artifact identifier, scope, and cardinality.

- `record` scope is supplied independently by every `InputRecord`.
- `shared` scope is supplied once in the `RunInputSnapshot`.
- Required scalar uses `minimum=1, maximum=1`.
- Optional scalar uses `minimum=0, maximum=1`.
- Repeated input uses a maximum greater than one or no maximum.

The model validates that minimum is nonnegative and maximum is absent or at least minimum. Compilation checks compatibility with the consuming procedure field; run creation checks concrete record counts.

## Reference language

References are deliberately not a general expression language.

```text
$inputs.<input-name>
$nodes.<node-id>.<output-field>
```

A scalar binding uses one reference. A repeated procedure field may use an ordered list. Ordering is semantic and is preserved through compilation, computation identity, materialization, and the tuple passed to the procedure.

Node-output references create graph dependencies. Pipeline-output references expose results but do not create additional work.

## Python builder

`PipelineBuilder` creates the same Pydantic domain model as YAML/JSON. Typed handles reduce spelling mistakes and obvious cross-builder misuse.

```python
from provium_pipeline.definition import PipelineBuilder

builder = PipelineBuilder(identifier="example.tokenize", version="1")
document = builder.record_input("document", DOCUMENT_ARTIFACT)
tokenize = builder.node(
    "tokenize",
    TOKENIZE_PROCEDURE,
    inputs={"document": document},
    config={"lowercase": True},
)
builder.output("tokens", tokenize.output("tokens"))
definition = builder.build()
```

Relevant handles are `PipelineInputHandle`, `PipelineRepeatedInputHandle`, `PipelineNodeHandle`, and `NodeOutputHandle`. Handles retain lightweight definitions, not concrete artifact/procedure implementation classes.

The builder checks identifier uniqueness, handle ownership, declared output fields, and obvious contract binding errors. Compilation remains authoritative because file-based definitions and catalog values pass through the same compiler.

## Catalogs and lazy definitions

`PipelineCatalog` registers definitions from:

- an in-memory `PipelineDefinition`;
- a lazy `module:attribute` target;
- a packaged JSON/YAML resource.

Catalog registration stores provenance so duplicate or failed definitions produce useful diagnostics. `discover_pipeline_catalogs()` loads the `provium.pipeline_catalogs` entry-point group and merges installed catalogs.

Artifact and procedure definitions come from Provium core catalogs. `ArtifactCatalogCollection` and `ProcedureCatalogCollection` search multiple catalogs, require a unique compatible definition, and preserve lightweight lookup.

## Configuration layers

Node `config` is the base configuration. Additional `PipelineConfigurationLayer` values are applied in order. Later mappings override earlier values using canonical mapping composition.

For each node the compiler:

1. resolves the procedure’s lightweight contract;
2. composes the inline and external mappings;
3. validates through the contract’s Pydantic configuration model;
4. includes defaults in the core `ConfigurationSnapshot`;
5. records schema and value digests;
6. records each raw layer’s canonical digest for audit.

An unconfigured procedure requires an empty resolved mapping. Existing runs use the stored snapshot rather than reloading configuration files or re-evaluating model defaults.

## Compiler validation

`PipelineCompiler.compile()` performs definition, catalog, graph, contract, configuration, and digest work. Important checks include:

- schema, identifier, and version validity;
- unique input/node/output names;
- known artifact and procedure identifiers;
- all required setup and processing fields bound;
- no unknown binding fields;
- exact artifact-definition compatibility;
- scalar, optional, and repeated cardinality compatibility;
- valid node-output and pipeline-output references;
- acyclic topology with a concrete cycle diagnostic;
- validated node configuration;
- deterministic output and preparation contracts.

Errors are returned as `PipelineCompilationError` containing structured `PipelineCompilationDiagnostic` entries rather than as one unstructured parser message.

## Deterministic topology

The compiler calculates dependencies from node-output references and uses a deterministic topological sort. When several nodes are ready, canonical node identifiers break ties. That order drives task planning and stable presentation, so equivalent inputs do not depend on dictionary or catalog iteration order.

## Compilation products

A `CompiledPipeline` records:

- identifier and version;
- canonical definition snapshot;
- complete definition digest;
- computation-relevant semantic digest;
- compiled input contracts;
- compiled nodes in deterministic topological order;
- compiled pipeline outputs;
- fully resolved configuration and layer audit.

Each compiled node contains procedure and contract identities, its configuration snapshot, ordered binding plans, expected output contracts, cache policy, and preparation/output contract digests.

## Definition digest versus semantic digest

The definition digest represents the complete canonical definition, including documented display/audit fields. The semantic digest represents computation-relevant topology, contracts, and validated configuration.

This split prevents harmless presentation changes from invalidating reusable computation while preserving exact source auditability.

## Why concrete classes are deferred

Compilation reads `ProcedureDefinition`, `ProcedureContract`, and `ArtifactDefinition` metadata. It does not need concrete targets. Concrete classes are resolved only while constructing a task invocation.

This boundary provides three practical benefits:

- validation works in environments without execution-only native dependencies;
- `pipeline validate` and quick help remain fast;
- a malicious or broken concrete import cannot affect semantic planning.

At execution, the installed contract digest is checked against the frozen task contract before the concrete procedure is used. This detects incompatible environment drift.

## Canonical representation

`canonical_definition_document()`, configuration canonicalization, and core canonical JSON rules normalize values before digesting. Stable ordering applies to inputs, nodes, fields, records, bindings, and outputs according to their documented semantic order.

The compiler output is therefore suitable for durable storage, comparison, export, and reconstruction across processes.
