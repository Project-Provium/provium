# Local and S3 files

Provium accepts local paths, `file://` URLs, and `s3://bucket/key` object locations.
Install the optional S3 dependency for standalone core usage:

```sh
pip install 'provium[s3]'
```

For pipeline usage, install `provium-pipeline[s3]`; S3 is optional in both
packages. Credentials and AWS region use the
standard boto3 configuration, such as environment variables, AWS profiles, or an
instance/task role.

## Temporary storage

S3 inputs are downloaded to private local temporary directories. Remote outputs
are written locally and uploaded after successful finalization. Temporary data
is removed when its operation or owning session ends.

The default is Python's system temporary directory (`tempfile.gettempdir()`).
Use `--scratch-directory /path/to/scratch` on commands, or
`provium.session(scratch_directory="/path/to/scratch")` in Python, to override it.
An explicitly supplied scratch directory is created if needed. Nested sessions
inherit the setting unless they specify their own directory. Local artifact
outputs retain adjacent staging files for atomic filesystem replacement.

## Core commands

Artifact import/export, procedure input/output bindings, configuration files,
and graph input/output files accept S3 locations:

```sh
provium artifact load provium_text_pipeline_example.DocumentV1 \
  s3://my-bucket/source.txt s3://my-bucket/source.pa \
  --scratch-directory /tmp/provium

provium execute provium_text_pipeline_example.TokenizeV1 \
  --input source=s3://my-bucket/source.pa \
  --output destination=s3://my-bucket/tokens.pa \
  --scratch-directory /tmp/provium

provium graph source s3://my-bucket/tokens.pa dot s3://my-bucket/lineage.dot
```

The first two commands require the example procedure/artifact package to be
installed. Artifact load/dump handlers still receive local paths, so existing
implementations need no S3-specific code. Procedure configuration layers supplied
through `--config` can also reside in S3.

## Python usage

Use the same artifact classes and bindings as for local files:

```python
import provium
from provium_text_pipeline_example.artifact import DOCUMENT

with provium.session(scratch_directory="/tmp/provium"):
    with DOCUMENT.resolve().open("s3://my-bucket/source.pa") as reader:
        text = reader.read()
```

Write bindings likewise accept S3 URIs; execute them through the normal procedure
lifecycle. Artifact inspection and procedure output results retain the durable
S3 URI instead of exposing a temporary filename. The JSON and YAML configuration
loaders also accept S3 locations.

For other file consumers, scoped helpers in `provium.files` provide local paths:

```python
from provium.files import local_file, writable_file

with local_file("s3://my-bucket/settings.yaml") as path:
    settings_text = path.read_text()

with writable_file("s3://my-bucket/report.txt") as path:
    path.write_text("Finished")
```

Both helpers accept `scratch_directory=`. A successful `writable_file` block
uploads its output; an exception abandons the temporary file.

## Pipeline configuration files

Every pipeline command accepting `-f` can read local and S3 files in one invocation:

```sh
provium pipeline register \
  -f s3://my-bucket/pipeline.yaml -f profiles.yaml \
  --scratch-directory /tmp/provium

provium pipeline run \
  -f s3://my-bucket/pipeline.yaml -f s3://my-bucket/profiles.yaml \
  --profile cluster --input document=s3://my-bucket/source.pa \
  --store s3://my-bucket/artifacts --scratch-directory /tmp/provium
```

Multi-document YAML, document ordering, duplicate detection, and validation work
the same way for both locations. Downloaded configuration files are removed after
parsing, including when validation fails. `unregister -f s3://...` selects the
registered objects named in the document; it does not delete the YAML object.

## Publication and ownership

Core finalizes an artifact before uploading it. Failed procedure execution does
not upload unfinished outputs. If publication of several outputs fails, core
attempts to restore earlier destination contents. S3 does not provide a
transaction spanning multiple objects; use the pipeline's committed result maps
and PostgreSQL coordination for shared distributed computation.

An S3 URI identifies one object, not a prefix to recursively download. HTTP URLs,
presigned URLs, and query/fragment parameters on S3 URIs are not accepted.
