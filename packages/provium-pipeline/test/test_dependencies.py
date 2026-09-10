"""Keep editable workspace installs compatible with the core release version."""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

PACKAGES = Path(__file__).resolve().parents[2]


def test_pipeline_dependency_accepts_core_0_9():
    pipeline = tomllib.loads(
        (PACKAGES / "provium-pipeline" / "pyproject.toml").read_text()
    )
    requirement = next(
        item
        for dependency in pipeline["project"]["dependencies"]
        if (item := Requirement(dependency)).name == "provium"
    )
    assert "0.9.0" in requirement.specifier


def test_pipeline_dependency_accepts_workspace_core_version():
    core = tomllib.loads((PACKAGES / "provium" / "pyproject.toml").read_text())
    pipeline = tomllib.loads(
        (PACKAGES / "provium-pipeline" / "pyproject.toml").read_text()
    )
    requirement = next(
        item
        for dependency in pipeline["project"]["dependencies"]
        if (item := Requirement(dependency)).name == "provium"
    )
    version = core["project"]["version"]
    assert version in requirement.specifier, (
        f"workspace provium {version} is excluded by {requirement}"
    )
