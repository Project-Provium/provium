from importlib.metadata import version

import provium_pipeline


def test_version_matches_distribution() -> None:
    assert provium_pipeline.__version__ == version("provium-pipeline")
    assert "__version__" in provium_pipeline.__all__
