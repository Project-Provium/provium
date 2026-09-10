"""Regression checks for CI coverage and release publication gates."""

import json
import unittest
from pathlib import Path

import yaml

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

    def test_pipeline_ci_covers_supported_python_versions(self):
        ci = workflow("test.yml")
        self.assertIn("pull_request", ci["on"])
        job = ci["jobs"]["pipeline-test"]
        self.assertEqual(job["strategy"]["matrix"]["python-version"], ["3.12", "3.13"])
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        self.assertIn("packages/provium[test]", commands)
        self.assertIn("packages/provium-pipeline[test]", commands)
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
        outputs = jobs["release-please"]["outputs"]
        self.assertIn(
            "packages/provium-pipeline--release_created",
            outputs["pipeline_release_created"],
        )

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


if __name__ == "__main__":
    unittest.main()
