# Provium Cloud

`provium-cloud` schedules procedure and unnamed pipeline invocations on AWS Batch.
It provides a Python client and native `provium cloud aws` commands, durable run
history and progress, shared computation deduplication, forced execution, and
pluggable state/artifact backends. It depends on `provium-pipeline` and has no
`provium-dispatch` dependency.

The runtime uses existing infrastructure. Deployment files are plain configuration;
they have no name or registration step. A separate repository-only AWS harness can
provision a disposable test environment, build/push an ECR image, test it, and tear
it down.

## Install and use

Python 3.12+ is required. Install from PyPI with the AWS and PostgreSQL backends:

```bash
python -m pip install 'provium-cloud[aws,postgres]'
```

For development, install the workspace packages from the repository root:

```bash
python -m pip install -e ./packages/provium \
  -e ./packages/provium-pipeline \
  -e './packages/provium-cloud[aws,postgres]'
```

Install your procedure/artifact packages in both the submitting environment and
worker image. For an existing configured deployment:

```bash
provium cloud aws plan -d deployment.yaml -f pipeline.yaml
provium cloud aws enqueue -d deployment.yaml -f pipeline.yaml \
  --input document=document.pa
# Run in a separate process with access to the same backends:
provium cloud aws controller -d deployment.yaml
```

Enqueueing records work; a running controller schedules it. PostgreSQL coordinates
distributed execution and S3 stores artifact bodies. SQLite and local files are
also supplied for local use. The package exports `CloudClient`, deployment models,
`load_deployment`, and `__version__`.

- [Cloud execution guide](https://project-provium.github.io/provium/guides/cloud/):
  deployment/graph examples, CLI and Python usage, progress, retries, and guarantees.
- [API reference](https://project-provium.github.io/provium/reference/provium-cloud/):
  client, planning, controller/worker, and custom backend contracts.
- [AWS testing guide](https://project-provium.github.io/provium/guides/cloud-testing/):
  profiles, IAM setup, dotenv layers, local/CI tests, reports, and cleanup recovery.

## Development and live testing

From the repository root, `make install` installs development dependencies. Run
`python -m pytest packages/provium-cloud/test`; Docker is required for the PostgreSQL
and LocalStack integration tests. Add `-m 'not integration'` to exclude those tests.
These commands do not provision real AWS resources.

For paid live tests, configure root `.env` using `.env.example` and follow the AWS
testing guide's credential/service-role requirements:

```bash
make cloud-live-install
make cloud-live-test
```

Environment precedence is shell, explicit `CLOUD_ENV`/`--env-file`, `.env.local`,
then `.env`. This loading is specific to the test harness. Recovery uses the
original run's printed results directory:

```bash
make cloud-live-cleanup CLOUD_RESULTS=/absolute/path/to/run
```

The optional `.github/workflows/cloud-live.yml` uses OIDC and explicit repository
variable opt-in. Keep the local journal until cleanup is confirmed; a hard process
or runner failure can leave resources that require recovery.
