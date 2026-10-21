"""Global configuration arguments and lazy access for command implementations."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import cast

_PREFIX = "_provium_config_"


def configure_configuration(parser: argparse.ArgumentParser) -> None:
    """Give each parser a separate destination so nested options accumulate."""
    parser.add_argument(
        "--config",
        action="append",
        type=_validate_override,
        default=[],
        dest=f"{_PREFIX}{parser.prog}",
        metavar="KEY.PATH=VALUE",
        help="Override application configuration (repeatable)",
    )


def _validate_override(value: str) -> str:
    from provium.configuration import parse_override

    parse_override(value)
    return value


def command_configuration(
    arguments: argparse.Namespace,
    *,
    environment_aliases: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Load files and environment, then apply overrides in command-line order."""
    from provium.configuration import load_configuration

    overrides = [
        value
        for name, values in vars(arguments).items()
        if name.startswith(_PREFIX)
        for value in cast(list[str], values)
    ]
    return load_configuration(
        overrides=overrides, environment_aliases=environment_aliases
    )
