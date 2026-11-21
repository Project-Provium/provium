"""Shared test inputs; external services are opt-in fixtures, never import effects."""

import pytest
from provium.cli import run


@pytest.fixture
def document(tmp_path):
    source = tmp_path / "document.txt"
    source.write_text("one two two", encoding="utf-8")
    artifact = tmp_path / "document.pa"
    assert (
        run(
            [
                "artifact",
                "load",
                "provium_text_pipeline_example.DocumentV1",
                str(source),
                str(artifact),
            ]
        )
        == 0
    )
    return artifact


@pytest.fixture
def test_catalog(monkeypatch):
    from procedures import EMPTY, OPTIONAL, VALUES
    from provium.procedure.discovery import discover_procedure_catalogs

    catalog = discover_procedure_catalogs()
    # Discovery returns a catalog collection; extend a local catalog through resolve.
    original = catalog.resolve

    def resolve(identifier):
        return {
            EMPTY.identifier: EMPTY,
            VALUES.identifier: VALUES,
            OPTIONAL.identifier: OPTIONAL,
        }.get(identifier) or original(identifier)

    monkeypatch.setattr(catalog, "resolve", resolve)
    for module in [
        "provium_cloud.invocations.planning",
        "provium_cloud.invocations.inputs",
        "provium_cloud.execution.worker",
        "provium_pipeline.planning",
    ]:
        monkeypatch.setattr(f"{module}.discover_procedure_catalogs", lambda: catalog)
    return catalog
