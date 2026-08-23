from __future__ import annotations

from typing import Any

import pytest

from provium_pipeline import PipelineCatalog as PublicPipelineCatalog
from provium_pipeline import PipelineCatalogError as PublicPipelineCatalogError
from provium_pipeline.catalog import PipelineCatalog, PipelineCatalogError
from provium_pipeline.definition import PipelineBuilder, PipelineDefinition


def pipeline(identifier: str) -> PipelineDefinition:
    return PipelineBuilder(identifier=identifier, version="1").build()


LAZY_PIPELINE = pipeline("lazy-pipeline")
NOT_A_PIPELINE: Any = object()


def test_catalog_types_are_publicly_exported() -> None:
    assert PublicPipelineCatalog is PipelineCatalog
    assert PublicPipelineCatalogError is PipelineCatalogError


def test_catalog_registers_in_memory_definition() -> None:
    catalog = PipelineCatalog()
    definition = pipeline("memory-pipeline")

    catalog.register(definition)

    assert catalog.get("memory-pipeline") is definition
    assert catalog.identifiers == ("memory-pipeline",)


def test_catalog_resolves_lazy_target_only_on_first_get() -> None:
    catalog = PipelineCatalog()
    catalog.register_target("lazy-pipeline", f"{__name__}:LAZY_PIPELINE")

    assert catalog.identifiers == ("lazy-pipeline",)
    assert catalog.get("lazy-pipeline") is LAZY_PIPELINE
    assert catalog.get("lazy-pipeline") is LAZY_PIPELINE


def test_catalog_loads_packaged_json_and_yaml_resources() -> None:
    catalog = PipelineCatalog()
    catalog.register_resource(
        "json-pipeline", "test.catalog_fixtures", "json_pipeline.json"
    )
    catalog.register_resource(
        "yaml-pipeline", "test.catalog_fixtures", "yaml_pipeline.yaml"
    )

    assert catalog.get("json-pipeline").pipeline.identifier == "json-pipeline"
    assert catalog.get("yaml-pipeline").pipeline.identifier == "yaml-pipeline"


def test_catalog_rejects_duplicate_identifiers() -> None:
    catalog = PipelineCatalog()
    catalog.register(pipeline("duplicate"))

    with pytest.raises(PipelineCatalogError, match="already registered.*duplicate"):
        catalog.register_target("duplicate", f"{__name__}:LAZY_PIPELINE")


def test_catalog_reports_unknown_identifier() -> None:
    with pytest.raises(PipelineCatalogError, match="not registered.*missing"):
        PipelineCatalog().get("missing")


@pytest.mark.parametrize(
    ("identifier", "target", "message"),
    [
        ("wrong-type", f"{__name__}:NOT_A_PIPELINE", "PipelineDefinition"),
        ("expected-id", f"{__name__}:LAZY_PIPELINE", "expected-id.*lazy-pipeline"),
        ("missing-target", "missing_catalog_module:value", "missing-target"),
    ],
)
def test_catalog_wraps_lazy_target_diagnostics(
    identifier: str, target: str, message: str
) -> None:
    catalog = PipelineCatalog()
    catalog.register_target(identifier, target)

    with pytest.raises(PipelineCatalogError, match=message):
        catalog.get(identifier)


def test_catalog_rejects_unknown_resource_extension() -> None:
    catalog = PipelineCatalog()
    catalog.register_resource("bad-resource", "test.catalog_fixtures", "unknown.txt")

    with pytest.raises(PipelineCatalogError, match="bad-resource.*unsupported"):
        catalog.get("bad-resource")


def test_catalog_rejects_non_definition_value() -> None:
    with pytest.raises(TypeError, match="PipelineDefinition"):
        PipelineCatalog().register(NOT_A_PIPELINE)


def test_catalog_wraps_missing_resource_diagnostic() -> None:
    catalog = PipelineCatalog()
    catalog.register_resource(
        "missing-resource", "test.catalog_fixtures", "missing.json"
    )

    with pytest.raises(PipelineCatalogError, match="missing-resource.*missing.json"):
        catalog.get("missing-resource")
