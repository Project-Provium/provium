from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pytest import MonkeyPatch, raises

import provium_pipeline.input.resolver as resolver_module
from provium_pipeline.input.resolver import (
    INPUT_RECORD_RESOLVER_ENTRY_POINT_GROUP,
    InputRecordResolverCatalog,
    discover_input_record_resolvers,
)
from test.test_input_resolver import Resolver


@dataclass(frozen=True)
class _EntryPoint:
    name: str
    value: str
    loaded: object

    def load(self) -> object:
        return self.loaded


def test_discovers_registered_input_record_resolvers_by_identifier() -> None:
    resolver = Resolver()
    catalog = discover_input_record_resolvers(
        entry_points=(_EntryPoint("test.resolver", "test:resolver", resolver),)
    )

    assert INPUT_RECORD_RESOLVER_ENTRY_POINT_GROUP == ("provium.input_record_resolvers")
    assert isinstance(catalog, InputRecordResolverCatalog)
    assert catalog.names() == ("test.resolver",)
    assert catalog.get("test.resolver") is resolver


def test_discovers_installed_entry_point_group(monkeypatch: MonkeyPatch) -> None:
    resolver = Resolver()
    observed: list[str] = []

    def entry_points(*, group: str) -> tuple[_EntryPoint, ...]:
        observed.append(group)
        return (_EntryPoint("test.resolver", "test:resolver", resolver),)

    monkeypatch.setattr(resolver_module, "installed_entry_points", entry_points)

    assert discover_input_record_resolvers().get("test.resolver") is resolver
    assert observed == [INPUT_RECORD_RESOLVER_ENTRY_POINT_GROUP]


def test_catalog_rejects_malformed_duplicate_and_unknown_resolvers() -> None:
    catalog = InputRecordResolverCatalog()
    resolver = Resolver()
    catalog.register(resolver)

    with raises(ValueError, match="already registered"):
        catalog.register(resolver)
    with raises(KeyError, match="unknown input-record resolver"):
        catalog.get("missing")

    class _InvalidIdentifier:
        identifier = ""

        def resolve(self, request: object, context: object) -> tuple[()]:
            return ()

    with raises(TypeError, match="identifier must be non-empty"):
        catalog.register(_InvalidIdentifier())

    class _MissingResolve:
        identifier = "missing.resolve"

    with raises(TypeError, match="has no resolve method"):
        catalog.register(cast(Any, _MissingResolve()))


def test_discovery_rejects_entry_point_identity_mismatch() -> None:
    with raises(ValueError, match="loaded identifier 'test.resolver'"):
        discover_input_record_resolvers(
            entry_points=(_EntryPoint("other.resolver", "test:resolver", Resolver()),)
        )
