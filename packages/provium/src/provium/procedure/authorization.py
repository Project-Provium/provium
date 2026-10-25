"""Private exact-binding authorization for procedure callbacks."""

from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from provium.artifact import (
        ArtifactReadBinding,
        ArtifactWriteBinding,
        ArtifactWriter,
    )


@dataclass(frozen=True, slots=True)
class _BindingAuthorization:
    inputs: Mapping[int, str | None]
    outputs: Mapping[int, ArtifactWriter]
    allow_binding_creation: bool


_ACTIVE_BINDINGS: ContextVar[_BindingAuthorization | None] = ContextVar(
    "provium_procedure_bindings",
    default=None,
)


@contextmanager
def authorize_bindings(
    inputs: tuple[ArtifactReadBinding[Any], ...],
    outputs: Mapping[str, ArtifactWriteBinding[Any]],
    writers: Mapping[str, ArtifactWriter],
    *,
    input_identities: Mapping[int, str] | None = None,
    allow_binding_creation: bool = True,
) -> Generator[None]:
    """Authorize the exact declared bindings for one callback activation."""
    authorized = _BindingAuthorization(
        {
            id(binding): (
                None if input_identities is None else input_identities[id(binding)]
            )
            for binding in inputs
        },
        {id(outputs[name]): writers[name] for name in outputs},
        allow_binding_creation,
    )
    token = _ACTIVE_BINDINGS.set(authorized)
    try:
        yield
    finally:
        _ACTIVE_BINDINGS.reset(token)


def assert_binding_creation_is_allowed() -> None:
    """Reject binding construction in a restricted declarative callback."""
    active = _ACTIVE_BINDINGS.get()
    if active is not None and not active.allow_binding_creation:
        raise RuntimeError(
            "artifacts cannot be bound, opened, or created manually inside a "
            "declarative procedure callback; use the provided input/output bindings"
        )


def expected_input_identity(binding: ArtifactReadBinding[Any]) -> str | None:
    """Return the preregistered identity for an authorized read binding."""
    active = _ACTIVE_BINDINGS.get()
    if active is not None and id(binding) not in active.inputs:
        raise RuntimeError(
            "artifact read binding was not declared for the active procedure callback"
        )
    return None if active is None else active.inputs[id(binding)]


def open_authorized_output[WriterT: ArtifactWriter](
    binding: ArtifactWriteBinding[WriterT],
) -> WriterT:
    """Return the staged writer authorized for an exact binding object."""
    active = _ACTIVE_BINDINGS.get()
    if active is None:
        raise RuntimeError(
            "artifact write binding requires an active procedure callback"
        )
    writer = active.outputs.get(id(binding))
    if writer is None:
        raise RuntimeError(
            "artifact write binding was not declared for the active procedure callback"
        )
    return cast(WriterT, writer)


__all__: list[str] = []
