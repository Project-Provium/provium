"""Explicit, layered application settings from user and project TOML files.

Importing this module performs no discovery or file I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path


def discover_configuration(
    *,
    user_directory: Path | None = None,
    start_directory: Path | None = None,
) -> tuple[Path, ...]:
    """Find sorted user files, then files in the nearest ancestor .provium.

    Only immediate *.toml files participate. An empty project directory is
    still a boundary; settings from enclosing projects are not inherited.
    """
    user = (
        Path.home() / ".config" / "provium"
        if user_directory is None
        else user_directory
    )
    start = Path.cwd() if start_directory is None else start_directory
    start = start.resolve()
    directories = [user]
    for parent in (start, *start.parents):
        project = parent / ".provium"
        if project.is_dir():
            directories.append(project)
            break
    return tuple(
        path
        for directory in directories
        for path in sorted(directory.glob("*.toml"))
        if path.is_file()
    )


def load_configuration(
    *,
    user_directory: Path | None = None,
    start_directory: Path | None = None,
    overrides: Iterable[str] = (),
    environment: Mapping[str, str] | None = None,
    environment_aliases: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Load settings afresh; recursively merge tables and replace other values.

    Later files override earlier files. Invalid TOML raises ValueError naming
    the source file; filesystem errors propagate to the caller.
    """
    import tomllib

    from provium.procedure.config import compose_configuration

    layers: list[dict[str, object]] = []
    for path in discover_configuration(
        user_directory=user_directory, start_directory=start_directory
    ):
        try:
            with path.open("rb") as stream:
                layers.append(tomllib.load(stream))
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"Invalid configuration in {path}: {error}") from error
    import os

    variables = os.environ if environment is None else environment
    for path, name in (environment_aliases or {}).items():
        if name in variables:
            # Legacy environment variables keep their original string semantics.
            layers.append(_override_layer(path, variables[name]))
    prefix = "PROVIUM_CONFIG__"
    for name in sorted(variables):
        if name.startswith(prefix):
            path = name.removeprefix(prefix).lower().replace("__", ".")
            layers.append(parse_override(f"{path}={variables[name]}"))
    layers.extend(parse_override(value) for value in overrides)
    return compose_configuration(layers)


def parse_override(assignment: str) -> dict[str, object]:
    """Parse KEY.PATH=VALUE; JSON values retain types, other values are strings."""
    import json

    path, separator, text = assignment.partition("=")
    if not separator:
        raise ValueError("configuration override must use KEY.PATH=VALUE")
    try:
        value = json.loads(text)
    except ValueError:
        value = text
    return _override_layer(path, value)


def _override_layer(path: str, value: object) -> dict[str, object]:
    import re

    parts = path.split(".")
    if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", part) is None for part in parts):
        raise ValueError(f"invalid configuration key path: {path!r}")
    result: dict[str, object] = {parts[-1]: value}
    for part in reversed(parts[:-1]):
        result = {part: result}
    return result
