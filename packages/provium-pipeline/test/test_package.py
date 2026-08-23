"""Package and core-compatibility contracts."""

from __future__ import annotations

import importlib.metadata

import pytest

import provium_pipeline
from provium_pipeline.compatibility import (
    IncompatibleCoreError,
    require_compatible_core,
)


def test_package_exposes_its_version() -> None:
    assert provium_pipeline.__version__ == importlib.metadata.version(
        "provium-pipeline"
    )


def test_package_is_typed() -> None:
    package_root = provium_pipeline.__path__[0]

    with open(f"{package_root}/py.typed", encoding="utf-8") as marker:
        assert marker.read() == ""


def test_current_core_is_compatible() -> None:
    require_compatible_core()


def test_missing_core_api_version_has_one_actionable_diagnostic() -> None:
    with pytest.raises(
        IncompatibleCoreError,
        match=(
            "provium-pipeline requires Provium core API version 1; "
            "installed Provium does not expose PROVIUM_CORE_API_VERSION"
        ),
    ):
        require_compatible_core(core_api_version=None)


def test_incompatible_core_has_one_actionable_diagnostic() -> None:
    with pytest.raises(
        IncompatibleCoreError,
        match=(
            "provium-pipeline requires Provium core API version 1; "
            "installed Provium exposes version 2"
        ),
    ):
        require_compatible_core(core_api_version=2)
