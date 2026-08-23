from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from provium_pipeline import PipelineCatalog
from provium_pipeline.discovery import (
    PIPELINE_CATALOG_ENTRY_POINT_GROUP,
    PipelineDiscoveryResult,
    discover_pipeline_catalogs,
)


@dataclass(frozen=True)
class FakeDistribution:
    name: str
    version: str


class FakeEntryPoint:
    group = PIPELINE_CATALOG_ENTRY_POINT_GROUP

    def __init__(
        self,
        name: str,
        value: str,
        provider: object,
        *,
        distribution: str = "example-dist",
        version: str = "1.2.3",
    ) -> None:
        self.name = name
        self.value = value
        self.dist = FakeDistribution(distribution, version)
        self._provider = provider
        self.load_count = 0

    def load(self) -> Any:
        self.load_count += 1
        if isinstance(self._provider, Exception):
            raise self._provider
        return self._provider


def test_discovery_merges_catalogs_without_resolving_definitions() -> None:
    provider = PipelineCatalog()
    provider.register_target("lazy-pipeline", "missing_implementation:pipeline")
    provider.register_resource(
        "resource-pipeline", "example_package", "pipelines/example.yaml"
    )
    entry_point = FakeEntryPoint(
        "example", "example_package.pipeline:catalog", provider
    )

    result = discover_pipeline_catalogs(entry_points=[entry_point])

    assert isinstance(result, PipelineDiscoveryResult)
    assert entry_point.load_count == 1
    assert result.catalog.identifiers == ("lazy-pipeline", "resource-pipeline")
    assert [diagnostic.definition_identifier for diagnostic in result.diagnostics] == [
        "lazy-pipeline",
        "resource-pipeline",
    ]
    first, second = result.diagnostics
    assert (
        first.entry_point,
        first.distribution,
        first.distribution_version,
        first.registration_kind,
        first.location,
        first.error_chain,
    ) == (
        "example",
        "example-dist",
        "1.2.3",
        "target",
        "missing_implementation:pipeline",
        (),
    )
    assert second.registration_kind == "resource"
    assert second.location == "example_package:pipelines/example.yaml"


def test_discovery_reports_duplicate_registration_sources() -> None:
    first = PipelineCatalog()
    first.register_target("duplicate", "first_module:pipeline")
    second = PipelineCatalog()
    second.register_target("duplicate", "second_module:pipeline")

    result = discover_pipeline_catalogs(
        entry_points=[
            FakeEntryPoint("first", "first:catalog", first),
            FakeEntryPoint("second", "second:catalog", second),
        ]
    )

    assert result.catalog.identifiers == ("duplicate",)
    duplicate = result.diagnostics[-1]
    assert duplicate.definition_identifier == "duplicate"
    assert duplicate.error_chain
    assert "first" in duplicate.error_chain[0]
    assert "second" in duplicate.error_chain[0]


def test_discovery_reports_provider_resolution_failure_chain() -> None:
    entry_point = FakeEntryPoint(
        "broken",
        "broken_package:catalog",
        RuntimeError("outer failure"),
        distribution="broken-dist",
        version="9.9",
    )

    result = discover_pipeline_catalogs(entry_points=[entry_point])

    assert result.catalog.identifiers == ()
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.entry_point == "broken"
    assert diagnostic.distribution == "broken-dist"
    assert diagnostic.distribution_version == "9.9"
    assert diagnostic.definition_identifier is None
    assert diagnostic.location == "broken_package:catalog"
    assert diagnostic.error_chain == ("RuntimeError: outer failure",)


def test_discovery_reports_wrong_provider_type() -> None:
    entry_point = FakeEntryPoint("wrong", "wrong:catalog", object())

    result = discover_pipeline_catalogs(entry_points=[entry_point])

    assert result.catalog.identifiers == ()
    diagnostic = result.diagnostics[0]
    assert diagnostic.definition_identifier is None
    assert diagnostic.error_chain == (
        "TypeError: entry point must provide a PipelineCatalog",
    )


def test_discovery_uses_installed_entry_point_group(monkeypatch: Any) -> None:
    calls: list[str] = []

    class EntryPoints:
        def select(self, *, group: str) -> tuple[()]:
            calls.append(group)
            return ()

    monkeypatch.setattr(
        "provium_pipeline.discovery.metadata.entry_points", lambda: EntryPoints()
    )

    result = discover_pipeline_catalogs()

    assert result.catalog.identifiers == ()
    assert calls == [PIPELINE_CATALOG_ENTRY_POINT_GROUP]
