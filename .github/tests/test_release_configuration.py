"""Regression checks for CI coverage and release publication gates."""

import json
import tomllib
import unittest
from pathlib import Path

import yaml
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    # BaseLoader preserves GitHub's `on` key rather than treating it as a boolean.
    return yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(), Loader=yaml.BaseLoader
    )


class ReleaseConfigurationTests(unittest.TestCase):
    def test_pipeline_is_an_independent_python_release(self):
        config = json.loads((ROOT / "release-please-config.json").read_text())
        package = config["packages"]["packages/provium-pipeline"]
        self.assertEqual(package["package-name"], "provium-pipeline")
        self.assertEqual(package["release-type"], "python")
        self.assertEqual(package["initial-version"], "0.1.0")
        self.assertIn("packages/provium", config["packages"])

    def test_core_release_updates_pipeline_dependency(self):
        config = json.loads((ROOT / "release-please-config.json").read_text())
        extras = config["packages"]["packages/provium"].get("extra-files", [])
        self.assertIn(
            {"type": "generic", "path": "/packages/provium-pipeline/pyproject.toml"},
            extras,
        )
        text = (ROOT / "packages/provium-pipeline/pyproject.toml").read_text()
        requirement = next(
            Requirement(value)
            for value in tomllib.loads(text)["project"]["dependencies"]
            if Requirement(value).name == "provium"
        )
        self.assertTrue(all(spec.operator == ">=" for spec in requirement.specifier))
        marked = [
            line for line in text.splitlines() if "x-release-please-version" in line
        ]
        self.assertEqual(len(marked), 1)
        self.assertIn(str(requirement), marked[0])

    def test_workspace_dependencies_accept_current_versions(self):
        projects = [
            tomllib.loads(path.read_text())["project"]
            for directory in ("packages", "examples")
            for path in (ROOT / directory).glob("*/pyproject.toml")
        ]
        versions = {project["name"]: project["version"] for project in projects}
        for project in projects:
            for value in project.get("dependencies", []):
                requirement = Requirement(value)
                if requirement.name in versions:
                    with self.subTest(package=project["name"], dependency=value):
                        self.assertIn(versions[requirement.name], requirement.specifier)

    def test_dependency_guard_runs_before_workspace_install(self):
        steps = workflow("test.yml")["jobs"]["lint-format"]["steps"]
        guard = next(
            i
            for i, step in enumerate(steps)
            if "unittest discover" in step.get("run", "")
        )
        install = next(
            i
            for i, step in enumerate(steps)
            if 'pip install -e "./packages/provium[test]"' in step.get("run", "")
        )
        self.assertLess(guard, install)

    def test_pipeline_ci_covers_supported_python_versions(self):
        ci = workflow("test.yml")
        self.assertIn("pull_request", ci["on"])
        job = ci["jobs"]["pipeline-test"]
        self.assertEqual(job["strategy"]["matrix"]["python-version"], ["3.12", "3.13"])
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        self.assertIn("packages/provium[test]", commands)
        self.assertIn("packages/provium-pipeline[test]", commands)
        self.assertIn("examples/provium-text-pipeline", commands)
        self.assertNotIn("services", job)
        self.assertNotIn("PROVIUM_TEST_DATABASE", job.get("env", {}))
        self.assertNotIn("AWS_ENDPOINT_URL_S3", job.get("env", {}))
        self.assertIn("packages/provium-dispatch[test]", commands)
        self.assertIn("--cov=provium_pipeline", commands)
        self.assertIn("--cov-fail-under=100", commands)
        lint = ci["jobs"]["lint-format"]["steps"]
        commands = [
            step["run"]
            for step in lint
            if step.get("working-directory") == "packages/provium-pipeline"
        ]
        self.assertTrue(any("ruff check" in command for command in commands))
        self.assertTrue(any("ruff format --check" in command for command in commands))
        self.assertTrue(any("pyright" in command for command in commands))

    def test_build_uses_release_outputs_and_tests_before_upload(self):
        jobs = workflow("release.yml")["jobs"]
        build = jobs["build"]
        self.assertEqual(build["needs"], "release-please")
        self.assertIn("releases_created == 'true'", build["if"])
        self.assertIn("paths_released", build["strategy"]["matrix"]["path"])
        checkout = build["steps"][0]
        self.assertIn("provium_tag", checkout["with"]["ref"])
        self.assertIn("pipeline_tag", checkout["with"]["ref"])
        steps = build["steps"]
        test_index = next(
            i for i, step in enumerate(steps) if "pytest" in step.get("run", "")
        )
        upload_index = next(
            i
            for i, step in enumerate(steps)
            if "upload-artifact" in step.get("uses", "")
        )
        self.assertLess(test_index, upload_index)
        commands = "\n".join(step.get("run", "") for step in steps)
        for check in ("ruff check", "ruff format --check", "pyright", "twine check"):
            self.assertIn(check, commands)
        self.assertIn("examples/provium-text-pipeline", commands)
        self.assertNotIn("services", build)
        self.assertNotIn("PROVIUM_TEST_DATABASE", build["env"])
        self.assertNotIn("AWS_ENDPOINT_URL_S3", build["env"])
        outputs = jobs["release-please"]["outputs"]
        self.assertIn(
            "packages/provium-pipeline--release_created",
            outputs["pipeline_release_created"],
        )

    def test_extension_releases_use_the_ci_integration_suite(self):
        build = workflow("release.yml")["jobs"]["build"]
        tests = [step for step in build["steps"] if "pytest" in step.get("run", "")]
        self.assertEqual(len(tests), 2)
        core = next(step for step in tests if "!=" not in step.get("if", ""))
        self.assertIn("matrix.path == 'packages/provium'", core["if"])
        self.assertIn('--cov="$PACKAGE_IMPORT"', core["run"])
        extensions = next(step for step in tests if "!=" in step.get("if", ""))
        self.assertIn("matrix.path != 'packages/provium'", extensions["if"])
        ci_steps = workflow("test.yml")["jobs"]["pipeline-test"]["steps"]
        ci = next(step for step in ci_steps if "pytest" in step.get("run", ""))
        self.assertEqual(extensions["working-directory"], ci["working-directory"])
        self.assertEqual(extensions["run"], ci["run"])

    def test_publication_is_separate_from_build_and_uses_oidc(self):
        jobs = workflow("release.yml")["jobs"]
        for name in ("publish-provium", "publish-pipeline"):
            with self.subTest(job=name):
                job = jobs[name]
                self.assertIn("build", job["needs"])
                self.assertIn("release_created == 'true'", job["if"])
                self.assertEqual(job["permissions"]["id-token"], "write")
                self.assertEqual(job["environment"]["name"], "pypi")
                uses = [step["uses"] for step in job["steps"]]
                self.assertTrue(any("download-artifact" in action for action in uses))
                self.assertTrue(
                    any("gh-action-pypi-publish" in action for action in uses)
                )
                self.assertFalse(any("checkout" in action for action in uses))

    def test_pipeline_waits_for_core_but_can_release_independently(self):
        job = workflow("release.yml")["jobs"]["publish-pipeline"]
        self.assertIn("publish-provium", job["needs"])
        for condition in (
            "always()",
            "!cancelled()",
            "needs.build.result == 'success'",
            "needs.publish-provium.result == 'success'",
            "needs.publish-provium.result == 'skipped'",
        ):
            self.assertIn(condition, job["if"])

    def test_dispatch_release_and_dependency_tracking(self):
        config = json.loads((ROOT / "release-please-config.json").read_text())
        package = config["packages"]["packages/provium-dispatch"]
        self.assertEqual(package["package-name"], "provium-dispatch")
        self.assertEqual(package["release-type"], "python")
        self.assertIn(
            {"type": "generic", "path": "/packages/provium-dispatch/pyproject.toml"},
            config["packages"]["packages/provium-pipeline"]["extra-files"],
        )
        jobs = workflow("release.yml")["jobs"]
        job = jobs["publish-dispatch"]
        self.assertIn("publish-pipeline", job["needs"])
        self.assertIn("publish-provium", job["needs"])
        self.assertIn("needs.publish-provium.result == 'success'", job["if"])
        self.assertIn("needs.publish-provium.result == 'skipped'", job["if"])
        self.assertIn("needs.build.result == 'success'", job["if"])
        self.assertIn("needs.publish-pipeline.result == 'skipped'", job["if"])
        self.assertEqual(
            job["environment"]["url"], "https://pypi.org/p/provium-dispatch"
        )
        self.assertEqual(job["permissions"]["id-token"], "write")
        self.assertIn("dispatch_tag", jobs["build"]["steps"][0]["with"]["ref"])
        self.assertIn("provium_dispatch", jobs["build"]["env"]["PACKAGE_IMPORT"])
        self.assertIn("provium-dispatch", jobs["build"]["env"]["PACKAGE_NAME"])

    def test_dispatch_has_ci_checks_and_full_coverage(self):
        jobs = workflow("test.yml")["jobs"]
        lint = [
            step["run"]
            for step in jobs["lint-format"]["steps"]
            if step.get("working-directory") == "packages/provium-dispatch"
        ]
        for command in ("ruff check", "ruff format --check", "pyright"):
            self.assertTrue(any(command in run for run in lint))
        tests = [
            step["run"]
            for step in jobs["pipeline-test"]["steps"]
            if step.get("working-directory") == "packages/provium-dispatch"
        ]
        self.assertTrue(any("--cov=provium_dispatch" in run for run in tests))
        self.assertTrue(any("--cov-fail-under=100" in run for run in tests))


if __name__ == "__main__":
    unittest.main()
