from typing import Any

import pytest

from provium import ArtifactCatalog, ArtifactDefinition, ProcedureCatalog
from provium_pipeline.compiler import (
    ArtifactCatalogCollection,
    CatalogResolutionError,
    ProcedureCatalogCollection,
)
from test.definition.test_builder import SOURCE, TRANSFORM


def test_artifact_catalog_collection_resolves_across_catalogs() -> None:
    first = ArtifactCatalog()
    second = ArtifactCatalog()
    first.register(SOURCE)
    other: ArtifactDefinition[Any] = ArtifactDefinition(
        identifier="example.OtherV1",
        target="example.artifact:OtherV1",
        description="other",
    )
    second.register(other)

    collection = ArtifactCatalogCollection((first, second))

    assert collection.resolve(SOURCE.identifier) is SOURCE
    assert collection.resolve(other.identifier) is other


def test_procedure_catalog_collection_resolves_definition() -> None:
    empty = ProcedureCatalog()
    catalog = ProcedureCatalog()
    catalog.register(TRANSFORM)

    collection = ProcedureCatalogCollection((empty, catalog))

    assert collection.resolve(TRANSFORM.identifier) is TRANSFORM


def test_procedure_catalog_collection_rejects_ambiguous_definition() -> None:
    first = ProcedureCatalog()
    second = ProcedureCatalog()
    first.register(TRANSFORM)
    second.register(TRANSFORM)
    collection = ProcedureCatalogCollection((first, second))

    with pytest.raises(
        CatalogResolutionError, match="ambiguous procedure.*example.TransformV1"
    ):
        collection.resolve(TRANSFORM.identifier)


@pytest.mark.parametrize(
    ("collection", "kind"),
    [
        (ArtifactCatalogCollection(()), "artifact"),
        (ProcedureCatalogCollection(()), "procedure"),
    ],
)
def test_catalog_collection_reports_missing_definition(
    collection: ArtifactCatalogCollection | ProcedureCatalogCollection,
    kind: str,
) -> None:
    with pytest.raises(CatalogResolutionError, match=f"{kind}.*missing"):
        collection.resolve("missing")


def test_catalog_collection_rejects_ambiguous_definition() -> None:
    first = ArtifactCatalog()
    second = ArtifactCatalog()
    first.register(SOURCE)
    second.register(SOURCE)
    collection = ArtifactCatalogCollection((first, second))

    with pytest.raises(
        CatalogResolutionError, match="ambiguous artifact.*example.SourceV1"
    ):
        collection.resolve(SOURCE.identifier)


def test_catalog_collections_are_immutable_snapshots() -> None:
    catalogs = [ArtifactCatalog()]
    collection = ArtifactCatalogCollection(catalogs)
    catalogs.append(ArtifactCatalog())

    assert len(collection.catalogs) == 1
