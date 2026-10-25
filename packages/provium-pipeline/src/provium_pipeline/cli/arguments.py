"""Shared argument declarations and validation, without execution imports."""

import argparse
import math
from typing import cast


def configure_connection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def configure_graph(parser: argparse.ArgumentParser) -> None:
    """Declare the common graph options, preserving the existing CLI interface."""
    configure_connection(parser)
    parser.add_argument("-f", "--file", action="append", default=[])
    parser.add_argument("--store")
    parser.add_argument("--scratch-directory")
    parser.add_argument(
        "--definition", help="Python module:attribute containing a PipelineDefinition"
    )
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--workers", action="append", default=[])
    parser.add_argument("--force", action="append", default=[])
    parser.add_argument("--idle-wait", type=float, default=0)
    parser.add_argument("--outputs", action="store_true")


def assert_graph_options_are_valid(arguments: argparse.Namespace) -> None:
    assignments(arguments.input)
    for value in arguments.workers:
        selector, separator, count = value.partition("=")
        if not selector or not separator or int(count) < 1:
            raise ValueError("worker override requires selector=positive-count")
    if not math.isfinite(arguments.idle_wait) or arguments.idle_wait < 0:
        raise ValueError("idle wait must be finite and nonnegative")


def assert_pipeline_is_selected(arguments: argparse.Namespace) -> None:
    assert_graph_options_are_valid(arguments)
    if not arguments.file:
        raise ValueError("select exactly one pipeline with -f")
    apply_configuration(arguments)


def apply_configuration(arguments: argparse.Namespace) -> None:
    """Resolve defaults once per invocation, after argparse has accepted syntax."""
    if getattr(arguments, "_provium_settings_loaded", False):
        return
    from provium.cli.configuration import command_configuration

    settings = command_configuration(
        arguments,
        environment_aliases={
            "pipeline.store": "PROVIUM_PIPELINE_STORE",
        },
    ).get("pipeline", {})
    if not isinstance(settings, dict):
        raise TypeError("pipeline configuration must be a table")
    for name in ("store", "scratch_directory"):
        value = cast(dict[str, object], settings).get(name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"pipeline.{name} must be a string")
        if hasattr(arguments, name) and getattr(arguments, name) is None:
            setattr(arguments, name, value)
    if hasattr(arguments, "store") and arguments.store is None:
        arguments.store = "./.provium/artifacts"
    arguments._provium_pipeline_settings = settings
    arguments._provium_settings_loaded = True


def assignments(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item or key in result:
            raise ValueError(f"invalid or duplicate binding: {value}")
        result[key] = item
    return result


def configure_storage(parser: argparse.ArgumentParser) -> None:
    configure_connection(parser)
    parser.add_argument("--store")
    parser.add_argument("--scratch-directory")
