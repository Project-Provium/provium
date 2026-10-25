"""Base type for artifact implementations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from os import PathLike
from pathlib import Path
from typing import Any, ClassVar, cast

from provium.artifact.binding import ArtifactReadBinding, ArtifactWriteBinding
from provium.artifact.reader import ArtifactReader
from provium.artifact.writer import ArtifactWriter
from provium.files import normalize_location


def _assert_text_is_valid(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be nonempty")


def _is_dotted_identifier(value: str) -> bool:
    return all(component.isidentifier() for component in value.split("."))


class Artifact[ReaderT: ArtifactReader, WriterT: ArtifactWriter]:
    """An artifact implementation with specialized reader and writer types."""

    definition: ClassVar[ArtifactDefinition[Any]]
    reader: type[ReaderT]
    writer: type[WriterT]
    dump: Callable[[ReaderT, Path], None] | None = None
    load: Callable[[WriterT, Path], None] | None = None

    @classmethod
    def open(cls, path: str | PathLike[str]) -> ReaderT:
        """Open this artifact for reading within the active resource session."""
        return cls.bind_read(path).open()

    @classmethod
    def create(cls, path: str | PathLike[str]) -> WriterT:
        """Open this artifact for writing within the active procedure callback."""
        return cls.bind_write(path).open()

    @classmethod
    def bind_read(cls, path: str | PathLike[str]) -> ArtifactReadBinding[ReaderT]:
        """Bind this artifact's reader type to a normalized path."""
        return ArtifactReadBinding(cls, normalize_location(path))

    @classmethod
    def bind_write(cls, path: str | PathLike[str]) -> ArtifactWriteBinding[WriterT]:
        """Bind this artifact's writer type to a normalized path."""
        return ArtifactWriteBinding(cls, normalize_location(path))


@dataclass(frozen=True, slots=True)
class ArtifactDefinition[ArtifactT: Artifact[Any, Any]]:
    """Describe an artifact implementation without importing it eagerly."""

    identifier: str
    target: str
    description: str

    def __post_init__(self) -> None:
        _assert_text_is_valid(self.identifier, "artifact definition identifier")
        _assert_text_is_valid(self.target, "artifact definition target")
        _assert_text_is_valid(self.description, "artifact definition description")

        module_name, separator, attribute_path = self.target.partition(":")
        if (
            not separator
            or not _is_dotted_identifier(module_name)
            or not _is_dotted_identifier(attribute_path)
            or ":" in attribute_path
        ):
            raise ValueError(
                "artifact definition target must use 'module:attribute' syntax"
            )

    def resolve(self) -> type[ArtifactT]:
        """Import and return the artifact class described by this definition."""
        module_name, _, attribute_path = self.target.partition(":")
        resolved: object = import_module(module_name)
        for component in attribute_path.split("."):
            resolved = getattr(resolved, component)
        resolved_definition = getattr(resolved, "definition", None)
        resolved_name = (
            f"{resolved.__module__}.{resolved.__qualname__}"
            if isinstance(resolved, type)
            else repr(resolved)
        )
        if not isinstance(resolved_definition, ArtifactDefinition):
            raise TypeError(
                f"resolved artifact target {resolved_name!r} for definition "
                f"{self.identifier!r} ({self.target!r}) does not declare an "
                "artifact definition"
            )
        if (
            resolved_definition.identifier != self.identifier
            or resolved_definition.target != self.target
        ):
            raise ValueError(
                f"resolved artifact target {resolved_name!r} definition identifier "
                f"and target do not match {self.identifier!r} ({self.target!r}); "
                f"received {resolved_definition.identifier!r} "
                f"({resolved_definition.target!r})"
            )
        return cast(type[ArtifactT], resolved)


__all__ = ["Artifact", "ArtifactDefinition"]
