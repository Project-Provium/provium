"""Cloud exposes its installed distribution version."""

from importlib.metadata import version

import provium_cloud


def test_version_matches_distribution() -> None:
    assert provium_cloud.__version__ == version("provium-cloud")
    assert "__version__" in provium_cloud.__all__
