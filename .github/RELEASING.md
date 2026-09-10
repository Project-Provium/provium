# Releasing the workspace packages

`release-please-config.json` tracks `packages/provium` and
`packages/provium-pipeline` independently. Conventional commits affecting each
package generate its release notes and version changes. The pipeline package's
first release is configured as `0.1.0`; release-please will add its version to
`.release-please-manifest.json` when creating that release PR. It is intentionally
not recorded as already released in the initial manifest.

## One-time publishing setup

On PyPI, configure a GitHub Trusted Publisher for **each** project:

| Field | Value |
| --- | --- |
| PyPI project | `provium` or `provium-pipeline` |
| Repository owner | `Project-Provium` |
| Repository name | `provium` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

For the new `provium-pipeline` project, register a **pending publisher** at
<https://pypi.org/manage/account/publishing/>. For an existing project, use its
Publishing settings. The GitHub repository should have a `pypi` environment with
any desired branch restrictions or approval requirements.

The publishing jobs use OIDC (`id-token: write`); no PyPI API-token secret is needed.
See the [PyPI Trusted Publishing documentation](https://docs.pypi.org/trusted-publishers/).

To have CI run automatically on release-please's own PRs, configure the repository
secret `RELEASE_PLEASE_TOKEN` with a GitHub PAT or GitHub App token authorized to
create release PRs and releases (Contents, Issues, and Pull requests permissions).
Without it, the workflow falls back to `GITHUB_TOKEN`: release automation and the
in-workflow build/publish jobs still work, but GitHub suppresses new workflow runs
from bot-created PR events. Normal human PRs and pushes still run CI. Also enable
"Allow GitHub Actions to create and approve pull requests" in repository Actions
settings if it is currently disabled.

## Release sequence

1. Merge conventional commits into `master`.
2. Review and merge the release-please PR, including the version and changelog.
3. The `Release` workflow creates the corresponding GitHub releases. It uses the
   action's per-package outputs directly; it does not rely on bot-generated tag or
   release events triggering another workflow.
4. For each released package, the build job checks out its exact release tag,
   installs workspace dependencies, checks Ruff and Pyright, runs tests with 100%
   coverage required, and builds/checks the wheel and source distribution.
5. Separate publishing jobs download those distributions and upload to PyPI.
   When both packages are released together, core publishes first. The pipeline
   can also release independently when core has no release in that run.

Pull-request/push CI tests the pipeline package on Python 3.12 and 3.13; release
builds repeat validation on Python 3.12 before uploading anything.

A compatible core version must already be available on PyPI for users to install
`provium-pipeline`. Its current dependency is `provium>=0.7,<0.9`. Publishing the
pipeline alone does not publish an unreleased core version automatically. Check
that requirement when reviewing release PRs, especially across core minor versions.

If an upload fails after the GitHub release was created, fix the publisher settings
or transient failure and use **Re-run failed jobs** on that workflow run. This
preserves the release outputs and built artifacts. Re-running all jobs may produce
no new release, so there would be nothing selected for publication. Existing PyPI
versions cannot be overwritten.

## Local workflow checks

With the core test dependencies installed:

```bash
python -m unittest discover -s .github/tests -v
ruff check --config packages/provium/pyproject.toml .github/tests
ruff format --check .github/tests
actionlint .github/workflows/test.yml .github/workflows/release.yml
```
