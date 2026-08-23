from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import ArtifactStore, ManagedArtifactDescriptor


def run_artifact_store_conformance(
    *,
    store: ArtifactStore,
    source: Path,
    descriptor: ManagedArtifactDescriptor,
    workspace: Path,
) -> None:
    """Validate the common lifecycle required of an artifact-store adapter."""
    first = store.publish(source=source, descriptor=descriptor)
    second = store.publish(source=source, descriptor=descriptor)
    if second.locator != first.locator:
        raise AssertionError("idempotent publication returned a different locator")

    store.stat(first)
    workspace.mkdir(parents=True, exist_ok=True)
    destination = workspace / "materialized.pa"
    store.materialize(
        descriptor=descriptor,
        locations=(first,),
        destination=destination,
    )
    if not destination.is_file():
        raise AssertionError("materialization did not create the requested file")

    store.delete(first)
    store.delete(first)
    from . import ArtifactStoreNotFoundError

    try:
        store.stat(first)
    except ArtifactStoreNotFoundError:
        return
    raise AssertionError("deleted artifact remains readable")
