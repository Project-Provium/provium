"""Immutable typed artifact path bindings."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, get_args, get_origin

from provium.artifact.reader import ArtifactReader
from provium.artifact.writer import ArtifactWriter
from provium.files import normalize_location

if TYPE_CHECKING:
    from provium.artifact.definition import Artifact


def assert_artifact_class_is_valid(artifact: object) -> type[Artifact[Any, Any]]:
    """Validate and return one concrete class-based artifact implementation."""
    from provium.artifact.definition import Artifact, ArtifactDefinition

    if not isinstance(artifact, type) or not issubclass(artifact, Artifact):
        raise TypeError(
            "artifact binding artifact must be an Artifact class; "
            f"received {artifact!r}"
        )
    artifact_class = cast(type[object], artifact)
    artifact_name = f"{artifact_class.__module__}.{artifact_class.__qualname__}"
    if not isinstance(getattr(artifact_class, "definition", None), ArtifactDefinition):
        raise TypeError(
            f"artifact class {artifact_name!r} must declare an artifact definition"
        )
    reader = getattr(artifact_class, "reader", None)
    if not isinstance(reader, type) or not issubclass(reader, ArtifactReader):
        raise TypeError(
            f"artifact class {artifact_name!r} must declare an artifact reader class; "
            f"received {reader!r}"
        )
    writer = getattr(artifact_class, "writer", None)
    if not isinstance(writer, type) or not issubclass(writer, ArtifactWriter):
        raise TypeError(
            f"artifact class {artifact_name!r} must declare an artifact writer class; "
            f"received {writer!r}"
        )
    specializations: set[tuple[type[object], type[object]]] = set()
    for base in artifact_class.__mro__:
        for generic_base in getattr(base, "__orig_bases__", ()):
            if get_origin(generic_base) is Artifact:
                arguments = get_args(generic_base)
                if len(arguments) == 2 and all(
                    isinstance(argument, type) for argument in arguments
                ):
                    specializations.add(
                        cast(tuple[type[object], type[object]], arguments)
                    )
    if len(specializations) != 1:
        raise TypeError(
            f"artifact class {artifact_name!r} must have exactly one generic "
            f"specialization; found {len(specializations)}"
        )
    expected_reader, expected_writer = next(iter(specializations))
    if reader is not expected_reader:
        raise TypeError(
            f"artifact class {artifact_name!r} reader does not match its generic "
            f"specialization; declared {reader!r}, expected {expected_reader!r}"
        )
    if writer is not expected_writer:
        raise TypeError(
            f"artifact class {artifact_name!r} writer does not match its generic "
            f"specialization; declared {writer!r}, expected {expected_writer!r}"
        )
    dump = getattr(artifact_class, "dump", None)
    if dump is not None and not callable(dump):
        raise TypeError(
            f"artifact class {artifact_name!r} dump must be callable or None; "
            f"received {dump!r}"
        )
    load = getattr(artifact_class, "load", None)
    if load is not None and not callable(load):
        raise TypeError(
            f"artifact class {artifact_name!r} load must be callable or None; "
            f"received {load!r}"
        )
    return cast(type[Artifact[Any, Any]], artifact)


def _validate_binding(artifact: object, path: object) -> Path | str:
    from provium.procedure.authorization import assert_binding_creation_is_allowed

    assert_binding_creation_is_allowed()
    assert_artifact_class_is_valid(artifact)
    if not isinstance(path, (str, PathLike)):
        raise TypeError("artifact binding path must be a string or path-like object")
    return normalize_location(cast(str | PathLike[str], path))


@dataclass(frozen=True, slots=True)
class ArtifactReadBinding[ReaderT: ArtifactReader]:
    """Bind a concrete artifact reader type to a local path or S3 URI."""

    artifact: type[Artifact[ReaderT, Any]]
    path: Path | str

    def __post_init__(self) -> None:
        normalized = _validate_binding(self.artifact, self.path)
        object.__setattr__(self, "path", normalized)

    def open(self) -> ReaderT:
        """Open this artifact within the active resource session."""
        from provium.session import current_session

        active = current_session()
        if active is None:
            raise RuntimeError("artifact opening requires an active session")
        return cast(ReaderT, active.open_artifact(self))


@dataclass(frozen=True, slots=True)
class ArtifactWriteBinding[WriterT: ArtifactWriter]:
    """Bind a concrete artifact writer type to a local path or S3 URI."""

    artifact: type[Artifact[Any, WriterT]]
    path: Path | str

    def __post_init__(self) -> None:
        normalized = _validate_binding(self.artifact, self.path)
        object.__setattr__(self, "path", normalized)

    def open(self) -> WriterT:
        """Open the staged writer authorized for the active procedure callback."""
        from provium.procedure.authorization import open_authorized_output

        return open_authorized_output(self)


__all__ = [
    "ArtifactReadBinding",
    "ArtifactWriteBinding",
    "assert_artifact_class_is_valid",
]
