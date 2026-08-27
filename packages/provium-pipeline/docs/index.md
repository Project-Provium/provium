# Provium Pipeline documentation

Prefer learning by running examples? Follow the [progressive Jupyter tutorial course](../notebooks/README.md), which assumes familiarity with standard Provium procedures and artifacts.

`provium-pipeline` is the managed workflow layer for [Provium](../../provium/README.md). Provium core defines artifacts, procedures, typed I/O, configuration, and one prepared procedure execution. This package adds the durable machinery required to turn those primitives into reproducible pipelines: definitions, compilation, frozen inputs, runs, tasks, dispatches, retries, managed artifact storage, queries, and recovery.

If the package feels large, start with the distinction between **semantic state** and **operational state**:

- Semantic state answers “what computation did the user ask for?” It includes the pipeline definition, validated configuration snapshots, exact input artifact identities, task contracts, and computation keys.
- Operational state answers “what is happening to that computation right now?” It includes run/task/dispatch states, leases, retry times, worker identities, workspaces, and physical artifact locations.

Keeping those concerns separate is what makes runs reproducible, execution resumable, and results reusable.

## Reading paths

### I want the big picture

1. [Concepts and vocabulary](concepts.md) explains why each domain object exists.
2. [Architecture](architecture.md) shows the layers and end-to-end flows.
3. [Definitions and compilation](definitions-and-compilation.md) explains how user intent becomes a checked immutable plan.
4. [Inputs and runs](inputs-and-runs.md) explains reproducibility and task planning.
5. [Execution](execution.md) explains dispatches, workers, retries, leases, and output acceptance.
6. [Artifacts](artifacts.md) explains managed storage, identity, materialization, retention, and garbage collection.

### I want to use the package

- [CLI reference and workflows](cli.md)
- [Operations and troubleshooting](operations.md)
- [Extension and plugin guide](extensions.md)

### I need implementation details

- [Storage and recovery](storage-and-recovery.md)
- [Testing architecture](testing.md)
- [Module and API reference](api-reference.md)

## The shortest useful mental model

A pipeline definition is compiled against lightweight artifact and procedure contracts. An input source is resolved once into immutable artifact identities. Together they produce a durable run containing one task per node and record. A dispatch selects which of those tasks may execute now. Workers lease tasks, materialize artifacts, call Provium core, import finalized outputs into managed storage, and atomically record accepted results. Queries and exports read the frozen state; retries and resumption do not reinterpret mutable source files.

```text
Pipeline definition + configuration layers
                  |
                  v
          Compiled pipeline
                  + frozen input snapshot
                  |
                  v
            Run and task graph
                  |
          selection/dependency policy
                  v
               Dispatch
                  |
          lease -> invoke -> publish
                  v
       Accepted outputs and run history
```

## Scope of the current distribution

The reference local environment uses SQLite for durable metadata and a content-addressed filesystem artifact store. The CLI drives a serial worker. The library also contains multiprocessing supervision and adapter protocols so other local runtimes can compose multiple worker processes. Provider-specific orchestration and remote storage belong in separate distributions.

Python 3.12 or newer and `provium>=0.7,<0.8` are required. Installing the wheel adds commands to the existing `provium` executable; it does not replace the core CLI.
