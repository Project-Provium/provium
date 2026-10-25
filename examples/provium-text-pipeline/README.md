# Provium Text Pipeline Example

An installable catalog with a two-step text-processing graph:

| Definition | Purpose |
| --- | --- |
| `DocumentV1` | UTF-8 document, with text load/dump handlers. |
| `TokensV1` | JSON token list, with newline-delimited text dumping. |
| `WordCountV1` | Word-count result. |
| `TokenizeV1` | Split a document into whitespace-delimited tokens. |
| `CountWordsV1` | Count the tokenized words. |

## Install

From the repository root:

```bash
python -m pip install -e packages/provium -e packages/provium-pipeline \
  -e 'examples/provium-text-pipeline[test]'
```

## Run the full graph

```bash
printf 'one two two\n' > document.txt
provium artifact load provium_text_pipeline_example.DocumentV1 document.txt document.pa
provium pipeline run -f examples/provium-text-pipeline/pipeline.yaml \
  --input document=document.pa \
  --output tokens=tokens.pa --output word_count=word-count.pa -v
provium artifact dump tokens.pa tokens.txt
```

The YAML contains only a pipeline graph; no compute profile is needed.
Requesting only `tokens` skips the word-count branch. Running again reuses cached
computations and checks publication destinations. Cached artifacts live under
`./.provium/artifacts` by default; requested destinations receive independent copies.

```bash
provium pipeline lookup text-processing.nodes.tokenize.destination \
  -f examples/provium-text-pipeline/pipeline.yaml --input document=document.pa
provium pipeline lookup text-processing.outputs.word_count \
  -f examples/provium-text-pipeline/pipeline.yaml --input document=document.pa --json
```

## Run a single procedure

```bash
provium execute provium_text_pipeline_example.TokenizeV1 \
  --input source=document.pa --output destination=single-tokens.pa
```

The loaded document begins a lineage through the load procedure. Token and
word-count artifacts retain their producing procedure and upstream inputs.

See the [pipeline guide](../../docs/guides/pipelines.md) for CSV requests and
Python execution, and [dispatch](../../docs/guides/dispatch.md) for durable queues.

## Test

From this example's directory, run `pytest`. Tests cover catalog discovery and
CLI artifact transfer, processing, output contents, and lineage.
