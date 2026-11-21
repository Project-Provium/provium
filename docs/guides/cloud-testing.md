# Testing cloud execution on AWS

The repository includes a disposable AWS test harness under
`packages/provium-cloud/infrastructure/aws`. It creates its own test infrastructure,
builds and pushes an ECR image from the checkout, runs a test driver inside AWS
Batch, downloads reports, and tears down the stack.

This is developer tooling, outside the installed `provium_cloud` runtime package.
For submitting your own workloads to existing infrastructure, see
[Cloud execution](cloud.md). Run the harness from a complete repository checkout;
it needs core, pipeline, cloud, and the text-pipeline example sources.

## What the live test covers

One end-to-end pytest scenario verifies:

- A two-node text pipeline executes and produces the expected word count.
- A standalone procedure and a differently shaped graph share equivalent work.
- Reusing a request ID returns the same run; cached outputs avoid new generations.
- Two forced pipeline invocations share a new generation per computation.
- Outputs can be fetched from S3 and read as typed artifacts.
- Run progress, input-set filtering, CLI enqueue/status, and cancel/retry work
  against shared PostgreSQL state. The cancellation check cancels an admitted run
  before scheduling it; it does not test killing a running Fargate container.

The local suite separately exercises failures, leases, submission reconciliation,
cleanup guards, and backend behavior. A passing live scenario is not exhaustive
fault injection or a guarantee about arbitrary external procedure side effects.
A local-initiated run completed successfully on November 20, 2026, including stack
teardown. The optional GitHub workflow has not yet been verified by a live CI run.

## Temporary architecture

| Resource | Purpose |
| --- | --- |
| Dedicated VPC and two subnets | Isolate the test network across two availability zones. |
| Fargate Batch compute environment and queue | Run the driver and procedure workers; maximum configured capacity is 4 vCPUs. |
| ECR repository | Hold the `linux/amd64` image tagged `test`, built from this checkout. |
| RDS PostgreSQL | Private, encrypted `db.t4g.micro` instance with 20 GiB storage. |
| RDS-managed Secrets Manager secret | Provide the database password inside tasks. |
| Versioned, encrypted S3 bucket | Hold artifacts and test reports. |
| Job/execution roles and CloudWatch log group | Supply task permissions and collect output. |

The driver runs the controller inside AWS so the database does not need public
access. Fargate tasks use public IPs for outbound image/API access; their security
group has no inbound rules. PostgreSQL accepts connections only from the task
security group. There is no NAT gateway. The container bootstrap fetches the
managed secret and supplies a TLS database connection in the task environment.

These resources incur AWS charges. Budget for provisioning and teardown as well as
job runtime; the driver timeout is not a whole-run cost or duration limit. The
stack is deliberately disposable: no RDS snapshots or artifact versions are
retained by teardown. Use it only for generated test data.

## Local prerequisites

You need Python 3.12+, Docker with Buildx and a running daemon, and AWS credentials
for the intended account. AWS CLI v2 is useful for authentication and diagnosis.
The target commercial AWS region must support the template's services and have
at least two available availability zones and sufficient quotas.

From the repository root, using the repository virtual environment:

```bash
# Create this only if you do not already have a virtual environment.
python3 -m venv .venv
make cloud-live-install
docker info
docker buildx version
```

`cloud-live-install` installs the SDK, dotenv loader, and CloudFormation validator.
It does not install the entire workspace; the Docker build installs the required
workspace packages into the image. If `.venv` already exists, use it directly.

### Authenticate and choose the account

List profiles and verify the one you intend to use:

```bash
aws configure list-profiles
aws sts get-caller-identity --profile default
```

The returned `Account` is `PROVIUM_CLOUD_TEST_ACCOUNT_ID`. An AWS profile is a local
credential configuration, not the CloudFormation service role. For an expired
console-login session, renew that profile using `aws login --profile default`.
If your organization uses IAM Identity Center, retain its `aws sso login --profile
YOUR_PROFILE` workflow instead.

Console-login profiles require a compatible Boto3 release and CRT support:

```bash
.venv/bin/python -m pip install 'boto3[crt]>=1.41,<2'
```

See the official [Boto3 credential guide](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html)
and [AWS CLI sign-in guide](https://docs.aws.amazon.com/signin/latest/userguide/command-line-sign-in.html).
You can also use temporary `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and
`AWS_SESSION_TOKEN` credentials. Keep one intentional credential source active;
AWS credential-chain precedence still applies after dotenv values are loaded.

### Prepare the CloudFormation service role

The harness requires an existing service role; it does not create its own bootstrap
role. There are two distinct permission sets:

| Identity | Required responsibility |
| --- | --- |
| Local caller, or GitHub OIDC role | Validate/create/describe/delete stacks and list stack resources/events; pass the CloudFormation role; authenticate and push to ECR; submit/describe/list/terminate jobs and disable the test queue; list/download/delete S3 objects and versions, list/abort multipart uploads; read CloudWatch logs; check caller identity. |
| CloudFormation service role | Create, inspect, and delete the template's networking, IAM roles/policies, Batch resources, RDS/subnet group, S3 bucket/policy, ECR repository, and log group; authorize RDS-managed secret creation and encryption. |

The service role trusts `cloudformation.amazonaws.com`. The caller needs
`iam:PassRole` for that specific role, plus its own operational permissions; attaching
permissions to the service role does not grant them to the caller. See
[AWS's service-role guidance](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-iam-servicerole.html).

A reusable trust document and permission template are included in
`packages/provium-cloud/infrastructure/aws/iam/`. The permission template was derived
from the successfully tested role. It scopes named resources to a stack prefix,
restricts role passing to test roles, and scopes networking mutations to the selected
account/region. Read/list operations can require `Resource: "*"`. This is a test
provisioning policy, not a permissions policy for the application workers.

An account administrator can render and review it as follows. These shell exports
also select the profile used by the following AWS CLI commands:

```bash
export AWS_PROFILE=default
export AWS_REGION=us-east-1
export PROVIUM_CLOUD_TEST_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
export PROVIUM_CLOUD_TEST_STACK_PREFIX=provium-cloud-test
```

Run this Python snippet with `.venv/bin/python` from the repository root:

```python
import json
import os
from pathlib import Path
from string import Template

source = Path("packages/provium-cloud/infrastructure/aws/iam/permissions.json")
output = Path(".cloud-live/bootstrap/permissions.json")
output.parent.mkdir(parents=True, exist_ok=True)
policy = Template(source.read_text()).substitute(
    ACCOUNT_ID=os.environ["PROVIUM_CLOUD_TEST_ACCOUNT_ID"],
    REGION=os.environ["AWS_REGION"],
    STACK_PREFIX=os.environ["PROVIUM_CLOUD_TEST_STACK_PREFIX"],
)
json.loads(policy)
output.write_text(policy)
print(output)
```

Review the rendered JSON, validate it, then create the role and attach the policy:

```bash
aws accessanalyzer validate-policy --region "$AWS_REGION" \
  --policy-type IDENTITY_POLICY \
  --policy-document file://.cloud-live/bootstrap/permissions.json

aws iam create-role --role-name provium-cloud-live-cloudformation \
  --assume-role-policy-document file://packages/provium-cloud/infrastructure/aws/iam/trust.json \
  --description 'CloudFormation service role for disposable Provium cloud tests'

aws iam put-role-policy --role-name provium-cloud-live-cloudformation \
  --policy-name ProviumCloudTestInfrastructure \
  --policy-document file://.cloud-live/bootstrap/permissions.json

aws iam get-role --role-name provium-cloud-live-cloudformation \
  --query Role.Arn --output text
```

Copy the returned ARN to `CLOUDFORMATION_ROLE_ARN`. If a role with this name already
exists, inspect its ownership and policies before updating it; skip `create-role`
when intentionally reusing your test role. Creating the role requires IAM privileges;
policy validation requires Access Analyzer privileges. The commands above do not
create a caller policy or change the caller's permissions.

Changing region or stack prefix requires corresponding service-role and caller
policy changes. Batch resources explicitly use the stack name so their ARNs match
these permissions. The bootstrap role remains available after test-stack teardown.

## Configure dotenv

Use the root `.env.example` as a template for `.env`; preserve any existing values
when merging it. A profile-based configuration needs:

```dotenv
AWS_PROFILE=default
AWS_REGION=us-east-1
PROVIUM_CLOUD_TEST_ACCOUNT_ID=123456789012
CLOUDFORMATION_ROLE_ARN=arn:aws:iam::123456789012:role/provium-cloud-live-cloudformation
```

Replace the example account ID and role ARN. A role name, user ARN, or service
principal is not a valid `CLOUDFORMATION_ROLE_ARN`.

| Variable | Default / meaning |
| --- | --- |
| `AWS_PROFILE` | Optional; selects an existing authenticated profile. Otherwise the normal SDK credential chain applies. |
| `AWS_REGION` | Required unless `AWS_DEFAULT_REGION` is set. A nonempty `AWS_REGION` takes precedence over that alias. |
| `PROVIUM_CLOUD_TEST_ACCOUNT_ID` | Required 12-digit account ID; checked against STS before provisioning and cleanup. |
| `CLOUDFORMATION_ROLE_ARN` | Required IAM role ARN in the test account. |
| `PROVIUM_CLOUD_TEST_STACK_PREFIX` | `provium-cloud-test`; 1–48 letters, numbers, or hyphens, starting with a letter. A random suffix distinguishes each run. |
| `PROVIUM_CLOUD_TEST_TIMEOUT` | `1800`; driver timeout in seconds, allowed range 60–3600. |

### Dotenv precedence

Highest precedence first:

1. Variables already present in the shell/process environment.
2. The explicit `CLOUD_ENV` / `--env-file` file.
3. Repository-root `.env.local`.
4. Repository-root `.env`.

Default files are optional. An explicit file must exist and adds an override layer;
it does not replace loading the default files. Values are literal: `${NAME}` is not
expanded and shell commands are not executed. An empty value overrides lower file
layers; a bare key without `=` is ignored. `.env` and `.env.*` are Git-ignored except
`.env.example`.

Both Make targets and direct harness commands locate the default files from the
repository root. With Make, a relative `CLOUD_ENV` is also resolved from the root.
With the direct Python command, a relative `--env-file` follows the current working
directory. These dotenv rules do not apply to ordinary `provium cloud aws` commands.

## Run locally

From the repository root:

```bash
make cloud-live-validate  # Local template validation; no AWS credentials needed.
make cloud-live-test
```

The test target validates the template again, checks Docker and the account,
creates a unique stack, builds/pushes the image, runs the driver, collects results,
and attempts cleanup even after a failure. The Docker context contains selected
workspace sources and test runtime files, not repository dotenv files or credentials.
ECR login credentials use a temporary Docker configuration.

Optional overrides:

```bash
make cloud-live-test CLOUD_ENV=.env.custom
make cloud-live-test CLOUD_RESULTS="$PWD/.cloud-live/manual-1"
```

Use a new results directory for each run; an existing `state.json` is never
overwritten by a new run. Prefer absolute `CLOUD_RESULTS` paths because Make launches
the harness from `packages/provium-cloud`. Without an override, results go to a new
`.cloud-live/<id>/` directory at the repository root.

The equivalent direct command, from `packages/provium-cloud`, is:

```bash
../../.venv/bin/python -m infrastructure.aws run
```

Leave the process running through teardown. Provisioning and deletion can take
longer than the test itself. A zero process exit means the driver passed, its JUnit
report was retrieved, and cleanup completed. The `AWS test resources deleted.`
message confirms successful teardown. A failed test still returns nonzero after
successful cleanup.

The driver timeout applies to the Batch attempt. The runner separately allows that
configured duration plus five minutes while waiting for the driver, including queue
time. Worker definitions use a 300-second timeout; each live-test completion phase
has a ten-minute limit. Provisioning, build/push, and deletion have their own bounds.

## Reports and cleanup recovery

The printed results directory can contain:

| File | Contents |
| --- | --- |
| `state.json` | Account/region, role, ownership token, stack identifier, outputs, driver job ID, and `passed` / `cleaned` flags when set. |
| `image.log` | Docker build, login, and push output. |
| `driver.json` | Terminal AWS Batch driver description. |
| `junit.xml`, `summary.json` | Test results downloaded from S3 before deletion. |
| `batch-logs.jsonl` | Collected CloudWatch events. |
| `stack-events.jsonl` | CloudFormation event snapshot collected before teardown. |

Files depend on how far the run progressed. `passed: true` alone does not establish
that teardown completed; check `cleaned: true` and the process exit. Journals contain
resource identifiers, not AWS credentials or the database password. Keep the journal
until cleanup is confirmed, and treat logs/reports as potentially sensitive output.

If cleanup fails, restore credentials/permissions and use the original directory:

```bash
make cloud-live-cleanup CLOUD_RESULTS=/absolute/path/to/original/run
# Add CLOUD_ENV=.env.custom if that is how you select the needed credentials.
```

Or, from the cloud package directory:

```bash
../../.venv/bin/python -m infrastructure.aws cleanup \
  --results /absolute/path/to/original/run
```

Recovery takes the account, region, stack, and role from the journal, while using
current credentials. It checks account and ownership tags, disables the queue,
terminates remaining active jobs, waits for them to stop, removes multipart uploads
and all bucket object versions/delete markers, and deletes the stack. The ECR
repository deletes its images with the stack. Recovery is repeatable; successful
cleanup marks the journal `cleaned`.

The harness attempts cleanup after exceptions, Ctrl-C, and SIGTERM. A machine crash,
SIGKILL, lost credentials, or AWS failure can prevent completion. There is no
independent cloud janitor. Do not delete the journal or use a new run as a substitute
for recovering the old one. Local Docker images/build caches, local reports, the
bootstrap role, and shared AWS service-linked roles are not removed by this cleanup.

## Optional GitHub Actions

The `Cloud live AWS tests` workflow lives in `.github/workflows/cloud-live.yml`.
It uses OIDC, not locally stored profiles or long-lived AWS access keys. Configure
an AWS role that trusts the intended GitHub repository/default branch and grants
the **caller** permissions described above. Its ARN is `AWS_ROLE_ARN`; it is distinct
from the CloudFormation service role. Match the repository's actual OIDC subject
format and restrict the audience to `sts.amazonaws.com`, following
[GitHub's AWS OIDC guide](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws).

Set these **repository Actions variables**, not secrets or environment-only values:

| Variable | Value |
| --- | --- |
| `PROVIUM_CLOUD_LIVE_TESTS` | `true` to enable paid live runs. |
| `AWS_ROLE_ARN` | GitHub OIDC caller role ARN. |
| `AWS_REGION` | Test region. |
| `PROVIUM_CLOUD_TEST_ACCOUNT_ID` | Test account ID. |
| `CLOUDFORMATION_ROLE_ARN` | Service role used to provision the stack. |

The job skips unless all required variables are present and the ref is the default
branch. It can be dispatched manually there, and runs automatically for relevant
source/workflow changes pushed to `master`. If your fork's default branch differs,
update the push branch filter too. Pull requests do not trigger live AWS tests.
The workflow uses the harness's default stack prefix and timeout; it does not map
additional repository variables for those settings.

Runs are serialized without canceling the previous run. The workflow refreshes
credentials and attempts cleanup again in an `always()` step when a journal exists,
then uploads `cloud-live-results` with seven-day retention. The job timeout is
150 minutes; the main run step allows 70 minutes and the recovery step 75 minutes.
Hard runner termination can still require downloading the journal and recovering
locally. The current recovery credential must belong to the journal's account.

## Local tests and troubleshooting

For tests that do not provision real AWS resources:

```bash
make install
.venv/bin/python -m pytest packages/provium-cloud/test -m 'not integration'
# Docker required for PostgreSQL and LocalStack service tests:
.venv/bin/python -m pytest packages/provium-cloud/test -m integration
```

The first suite uses local/fake compute and includes the offline harness tests.
Neither command launches the live driver. `make cloud-live-test` is the explicit
entry point for paid AWS tests.

| Symptom | Check / next action |
| --- | --- |
| Missing or malformed role ARN | Use the full ARN of an existing role in the configured account. |
| CLI works but Python reports a missing login dependency | Install the Boto3 CRT extra shown above. |
| Expired session or wrong account | Renew/select the intended profile. Inspect shell variables that override `.env`. |
| `AccessDenied` while provisioning | Inspect failed CloudFormation events and the service-role policy, including its prefix/region scope. |
| ECR push or report/cleanup access denied | Inspect the caller's permissions, not just the CloudFormation service role. |
| Docker failure | Check `docker info`, `docker buildx version`, and `image.log`. Builds target `linux/amd64`, including from ARM hosts. |
| Driver or worker fails | Read `driver.json`, `batch-logs.jsonl`, and `junit.xml` if collected. |
| Jobs stay queued | Inspect Batch status reasons, compute-environment health, and account Fargate quotas. |
| Cleanup error or process interrupted | Keep `state.json` and run `cloud-live-cleanup` with its directory. |
| CI skips | Check the five repository variables, opt-in value, and selected/default branch. |

For a stack still present, AWS CLI can retrieve failed-operation details:

```bash
aws cloudformation describe-events --region us-east-1 \
  --stack-name YOUR_STACK_NAME_OR_ARN --filters FailedEvents=true
```

Use the region and stack identifier from the journal. For a deleted stack, use its
full ARN. The saved stack events precede deletion, so inspect current AWS events
when diagnosing a teardown failure.
