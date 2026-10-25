"""Dispatch exposes its installed distribution version."""

from importlib.metadata import version

import provium_dispatch


def test_version_matches_distribution_metadata():
    assert provium_dispatch.__version__ == version("provium-dispatch")
    assert "__version__" in provium_dispatch.__all__
