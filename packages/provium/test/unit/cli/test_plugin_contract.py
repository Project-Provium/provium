"""Contract tests for versioned CLI plugins."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

import provium
from provium.cli import (
    CLI_PLUGIN_API_VERSION,
    CLI_PLUGIN_ENTRY_POINT_GROUP,
    CLIPlugin,
    CommandCatalog,
    discovery,
)


class _EntryPoint:
    def __init__(
        self, name: str, value: object, distribution: str = "example-plugin"
    ) -> None:
        self.name = name
        self._value = value
        self.dist = SimpleNamespace(name=distribution)

    def load(self) -> object:
        return self._value


def _install_entry_points(
    monkeypatch: pytest.MonkeyPatch, *entry_points: _EntryPoint
) -> None:
    def installed_entry_points(*, group: str) -> tuple[_EntryPoint, ...]:
        assert group == CLI_PLUGIN_ENTRY_POINT_GROUP
        return entry_points

    monkeypatch.setattr(discovery.metadata, "entry_points", installed_entry_points)
    discovery.reset_command_discovery()


def test_cli_plugin_contract_is_public_and_immutable() -> None:
    catalog = CommandCatalog()
    plugin = CLIPlugin(
        api_version=CLI_PLUGIN_API_VERSION,
        implementation="example.cli:catalog",
        catalog=catalog,
    )

    assert CLI_PLUGIN_API_VERSION == 1
    assert CLI_PLUGIN_ENTRY_POINT_GROUP == "provium.cli.command_catalogs"
    assert plugin.catalog is catalog
    assert provium.CLIPlugin is CLIPlugin
    assert provium.CLI_PLUGIN_API_VERSION == CLI_PLUGIN_API_VERSION
    assert provium.CLI_PLUGIN_ENTRY_POINT_GROUP == CLI_PLUGIN_ENTRY_POINT_GROUP

    with pytest.raises(FrozenInstanceError):
        plugin.api_version = 2  # type: ignore[misc]


@pytest.mark.parametrize("implementation", ["", "   "])
def test_cli_plugin_rejects_blank_implementation(implementation: str) -> None:
    with pytest.raises(ValueError, match="implementation must not be blank"):
        CLIPlugin(
            api_version=CLI_PLUGIN_API_VERSION,
            implementation=implementation,
            catalog=CommandCatalog(),
        )


def test_cli_plugin_rejects_an_invalid_catalog() -> None:
    with pytest.raises(TypeError, match="catalog must be a CommandCatalog"):
        CLIPlugin(
            api_version=CLI_PLUGIN_API_VERSION,
            implementation="example.cli:catalog",
            catalog=object(),  # type: ignore[arg-type]
        )


def test_discovery_accepts_a_supported_cli_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = CLIPlugin(
        api_version=CLI_PLUGIN_API_VERSION,
        implementation="example.cli:catalog",
        catalog=CommandCatalog(),
    )
    _install_entry_points(monkeypatch, _EntryPoint("example", plugin))

    assert isinstance(discovery.discover_command_catalogs(), CommandCatalog)


def test_discovery_keeps_legacy_command_catalog_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entry_points(monkeypatch, _EntryPoint("legacy", CommandCatalog()))

    assert isinstance(discovery.discover_command_catalogs(), CommandCatalog)


def test_discovery_rejects_an_unsupported_cli_plugin_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = CLIPlugin(
        api_version=CLI_PLUGIN_API_VERSION + 1,
        implementation="future.cli:catalog",
        catalog=CommandCatalog(),
    )
    _install_entry_points(monkeypatch, _EntryPoint("future", plugin, "future-dist"))

    with pytest.raises(
        RuntimeError,
        match=(
            "CLI plugin entry point 'future' from distribution 'future-dist' uses "
            "unsupported API version 2; this Provium installation supports version 1"
        ),
    ):
        discovery.discover_command_catalogs()


def test_discovery_rejects_an_invalid_cli_plugin_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entry_points(monkeypatch, _EntryPoint("invalid", object(), "broken-dist"))

    with pytest.raises(
        TypeError,
        match=(
            "CLI plugin entry point 'invalid' from distribution 'broken-dist' "
            "must expose a CLIPlugin or legacy CommandCatalog"
        ),
    ):
        discovery.discover_command_catalogs()
