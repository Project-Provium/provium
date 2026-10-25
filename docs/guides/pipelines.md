# Pipelines

`provium-pipeline` executes a typed procedure graph for explicit inputs and publishes requested artifacts. It provides caching, batch requests, worker scheduling, and extension protocols. It does not require a database or compute profile.

Use [Dispatch](dispatch.md) for registered pipelines, durable queues, compute profiles, and persistent input-set names.

## Install

```bash
pip install provium-pipeline
```

For the examples below, from a repository checkout:

```bash
python -m pip install -e packages/provium -e packages/provium-pipeline -e examples/provium-text-pipeline
```

Add `provium[s3]` for S3 storage. Procedure and artifact implementations must be installed wherever workers execute.

## Define a graph

Save this as `pipeline.yaml`, or use `examples/provium-text-pipeline/pipeline.yaml`:

```yaml
kind: PipelineConfiguration
name: text-processing
inputs:
  document: provium_text_pipeline_example.DocumentV1
nodes:
  tokenize:
    procedure: provium_text_pipeline_example.TokenizeV1
    inputs:
      source: $inputs.document
  count_words:
    procedure: provium_text_pipeline_example.CountWordsV1
    inputs:
      tokens: $nodes.tokenize.destination
targets:
  tokens: $nodes.tokenize.destination
  word_count: $nodes.count_words.word_count
```

Each node names a catalog-discovered procedure. Its `inputs` and `setup_inputs` bind procedure ports to `$inputs.NAME` or `$nodes.NODE.PORT`. Repeated ports accept lists of references; optional bindings can be omitted or null. Node `configuration` contains procedure settings, and `tags` provides selector labels.

`targets` gives public names to graph outputs. The older YAML key `outputs` remains accepted. Python models and lookup selectors still use the term `outputs`. Graph validation checks types, ports, cardinality, references, cycles, and whether nodes contribute to declared targets.

## Execute and publish

Create a raw text file, convert it to a Provium artifact, then execute:

```bash
printf 'one two two\n' > document.txt
provium artifact load provium_text_pipeline_example.DocumentV1 document.txt document.pa
provium pipeline run -f pipeline.yaml \
  --input document=document.pa \
  --output tokens=tokens.pa \
  --output word_count=word-count.pa -v
provium artifact dump tokens.pa tokens.txt
```

A direct CLI run requires at least one output destination, supplied with `--output` or in a CSV. The runtime executes the requested outputs and their dependencies. Requesting only `tokens` does not execute `count_words`. You can also request an intermediate output directly:

```bash
provium pipeline run -f pipeline.yaml --input document=document.pa \
  --output nodes.tokenize.destination=intermediate.pa
```

Output destinations receive serialized Provium artifacts, regardless of filename extension. Use `provium artifact dump` to convert them to a procedure's supported external format.

Destinations must be outside the managed cache, must not overwrite any request input, and must be unique across the request batch. Equivalent existing artifacts can be reused at their destinations.

Outputs are first committed to the cache, then copied to destinations. Published files are independent of cache retention. Existing destinations are checked for compatibility; incompatible files fail unless the applicable computation is forced. Publication is not an all-or-nothing transaction: if one destination fails, earlier successful copies remain and the request fails.

## CSV batches

```csv
inputs.document,tokens,word_count,nodes.tokenize.destination
first.pa,first-tokens.pa,first-count.pa,
second.pa,,second-count.pa,second-intermediate.pa
```

```bash
provium pipeline run -f pipeline.yaml --csv requests.csv --strategy stage
```

Input headers are `inputs.NAME`. Output headers are a target name or `nodes.NODE.PORT`; the cell is the destination path. An empty output cell means “do not publish this output for this row.” Every row must request at least one destination. Headers must be nonempty and unique, and row widths must match the header.

Paths are relative to the process working directory, not the CSV's directory. Use either CSV requests or individual CLI bindings. Requests are validated before execution; this is not a streaming, constant-memory CSV reader. Other request formats can implement the [RequestSource protocol](../reference/provium-pipeline.md#requests-and-publication).

## Scheduling and selection

| Option | Behavior |
| --- | --- |
| `--strategy stage` | Default. Process a stage across inputs before advancing, reducing repeated procedure/model loading. |
| `--strategy item` | Advance one input through its requested graph before moving to another. |
| `--workers nodes.tokenize=2` | Override the worker count for a node. |
| `--only nodes.tokenize` | Restrict procedures allowed to execute. |
| `--force nodes.tokenize` | Recompute selected work instead of reusing its result. |
| `--verify-artifacts` | Read and verify cached artifacts during reuse. |
| `--verbose` / `-v` | Report skipped procedures whose cached outputs were reused. |

Selectors also support tags, for example `tags.cpu`; selection options are repeatable. Targets select what to produce, while `--only` restricts what may execute. If a required dependency cannot be reused and its procedure is excluded, the input fails. It is not silently counted as successful.

Worker processes execute procedures; the parent manages scheduling and outcomes. Stage scheduling is useful for expensive setup such as loading models. Compute profiles belong to dispatch; basic Python execution uses `ExecutionSettings`.

## Cache and identity

The default store is `./.provium/artifacts`. Local direct execution writes:

```text
.provium/artifacts/
  artifacts/<computation>/<generation>/<artifact-digest>.pa
  results/<computation>.json
```

A result manifest records one computation's accepted outputs, not a whole run. Computation keys depend on the procedure, configuration, and input fingerprints. They are stable across equivalent executions. Generation directories distinguish writes; run IDs identify individual executions and change on each run.

An input-set hash identifies the logical input bindings and their fingerprints. It excludes file paths, filenames, artifact UUIDs, pipeline identity, and run identity. Input names still matter. It is distinct from the computation key and artifact digest.

Cached output metadata supplies the fingerprints needed for downstream keys. The graph alone cannot predict arbitrary output contents. By default, a cached rerun does not fetch and read every intermediate artifact body. Original inputs are inspected, and cached artifacts are materialized and verified when consumed or published. Use `--verify-artifacts` for eager verification. Metadata-only lookup can return a location whose file has subsequently disappeared.

`--force` replaces accepted results for selected computations. A missing or invalid dependency may be recomputed when its producer is permitted; otherwise the input fails. Treat cache contents as managed data.

## Inspect and locate outputs

```bash
provium pipeline validate -f pipeline.yaml
provium pipeline inspect -f pipeline.yaml
provium pipeline status -f pipeline.yaml --input document=document.pa --outputs
provium pipeline lookup text-processing.outputs.word_count \
  -f pipeline.yaml --input document=document.pa
provium pipeline lookup text-processing.nodes.tokenize.destination \
  -f pipeline.yaml --input document=document.pa --json
```

Lookup reads result metadata and prints the artifact URI, or metadata with `--json`. It does not run missing procedures or copy the artifact. Named input sets are available through [dispatch lookup](dispatch.md#name-input-sets-and-look-up-artifacts).

## Logs, errors, and results

Logs go to stderr by default. Human-readable results go to stdout; `run --json` emits machine-readable results there. Known input errors are concise, while unexpected worker failures can retain diagnostic tracebacks. Failed outcomes include an error message. Verbose reuse messages belong to diagnostics, not the JSON stream.

A failed input or publication makes the command fail. Invalid configuration or executor-level errors also return a nonzero exit status. Inspect individual outcome states in automation; partial or cancelled work is not equivalent to completed output publication. Python callers can supply `log_path` to write logs to a file explicitly.

## Storage and cleanup

Set `[pipeline].store` in project configuration or pass `--store`. Local paths and S3 stores are supported; `--scratch-directory` selects temporary download storage. See [Configuration](configuration.md) and [Local and S3 files](storage.md).

```bash
provium pipeline cache clear
provium pipeline cache clear --older-than 86400
provium pipeline cache clear --max-bytes 1073741824
# Replace HASH with a full computation key; --key is repeatable.
provium pipeline cache clear --key HASH
```

Without filters, clear removes eligible computation entries. Age is measured from modification time, not last access. A byte target removes oldest eligible entries until the target is met or candidates are exhausted; filters can leave more bytes than requested. This is explicit maintenance, not an automatic disk quota.

Add `--discard-cache` to a run to remove its touched computation entries after the whole run succeeds, including entries reused during that run. Failed runs retain cache. Publication copies survive cleanup. Cleanup currently supports local file stores, not S3 or database blobs.

Direct execution and cleanup use a store lock. The `.writer.lock` file can remain after release; its existence does not mean a worker still holds the lock. Do not delete lock files to bypass coordination. A dispatch-managed store requires [dispatch cleanup](dispatch.md#cache-maintenance-and-space).

When storage is exhausted, execution fails with an out-of-space error. Retained local cache may produce a cleanup hint; no setting can guarantee space when live work occupies the available storage.

## Python execution

Keep multiprocessing entry points behind a main guard:

```python
from provium_pipeline.preparation import prepare_pipeline
from provium_pipeline.requests import ExecutionRequest
from provium_pipeline.runtime import ExecutionContext
from provium_pipeline.store import ArtifactStore


def main():
    prepared = prepare_pipeline(files=["pipeline.yaml"])
    with ArtifactStore("./.provium/artifacts") as store:
        with ExecutionContext(
            prepared.plan.configuration,
            store,
            definition=prepared.definition,
            strategy="stage",
        ) as execution:
            execution.start([
                ExecutionRequest(
                    inputs={"document": "document.pa"},
                    destinations={"word_count": "word-count.pa"},
                )
            ])
        for outcome in execution.outcomes:
            print(outcome.state, outcome.error)


if __name__ == "__main__":
    main()
```

`start()` returns immediately; context exit joins the execution. Handle both failed outcomes and exceptions at shutdown. The Python APIs take explicit settings and do not automatically load CLI configuration.

See the [pipeline API reference](../reference/provium-pipeline.md) for contracts and extension points, and [Dispatch](dispatch.md) for durable execution.
