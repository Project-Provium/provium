"""Shared argument declarations and validation, without execution imports."""

import argparse
import math
import os


def configure_connection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", default=os.environ.get("PROVIUM_DATABASE_URL"))
    parser.add_argument("--json", action="store_true")


def configure_graph(parser: argparse.ArgumentParser) -> None:
    """Declare the common graph options, preserving the existing CLI interface."""
    configure_connection(parser)
    parser.add_argument("pipeline", nargs="?")
    parser.add_argument("-f", "--file", action="append", default=[])
    parser.add_argument(
        "--store",
        default=os.environ.get("PROVIUM_PIPELINE_STORE") or "./.provium/artifacts",
    )
    parser.add_argument("--scratch-directory")
    parser.add_argument(
        "--definition", help="Python module:attribute containing a PipelineDefinition"
    )
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--profile")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--workers", action="append", default=[])
    parser.add_argument("--force", action="append", default=[])
    parser.add_argument("--idle-wait", type=float, default=0)
    parser.add_argument("--outputs", action="store_true")


def require_database(arguments: argparse.Namespace) -> None:
    if not arguments.database:
        raise ValueError("database required; use --database or PROVIUM_DATABASE_URL")


def validate_graph_options(arguments: argparse.Namespace) -> None:
    assignments(arguments.input)
    for value in arguments.workers:
        selector, separator, count = value.partition("=")
        if not selector or not separator or int(count) < 1:
            raise ValueError("worker override requires selector=positive-count")
    if not math.isfinite(arguments.idle_wait) or arguments.idle_wait < 0:
        raise ValueError("idle wait must be finite and nonnegative")


def require_pipeline(arguments: argparse.Namespace) -> None:
    validate_graph_options(arguments)
    if arguments.pipeline:
        require_database(arguments)
        if arguments.file:
            raise ValueError(
                "registered execution cannot combine files or direct inputs"
            )
    elif not arguments.file:
        raise ValueError("select exactly one pipeline with -f or a registered name")


def assignments(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item or key in result:
            raise ValueError(f"invalid or duplicate binding: {value}")
        result[key] = item
    return result
