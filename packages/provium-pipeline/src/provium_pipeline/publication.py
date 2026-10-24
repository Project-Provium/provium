"""Publish retained copies without changing cache ownership."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from provium.files import (
    file_exists,
    location_identity,
    normalize_location,
    publish_file,
)

from .requests import InputSet
from .store import Artifact, ArtifactStore


class Publisher(Protocol):
    def validate(self, requests: Sequence[InputSet]) -> None: ...

    def publish(
        self, artifact: Artifact, destination: str, *, overwrite: bool = False
    ) -> Artifact: ...


class FilePublisher:
    """Copy through the core file protocols; local installation is atomic."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def validate(self, requests: Sequence[InputSet]) -> None:
        inputs: set[Path | str] = set()
        destinations: set[Path | str] = set()
        for request in requests:
            for value in request.inputs.values():
                location = (
                    value.location
                    if isinstance(value, Artifact)
                    else value["location"]
                    if isinstance(value, dict)
                    else value
                )
                inputs.add(location_identity(location))
        for request in requests:
            for destination in (request.output_destinations or {}).values():
                identity = location_identity(destination)
                self._outside_cache(identity)
                if identity in inputs:
                    raise ValueError(
                        f"output destination overwrites an input: {destination}"
                    )
                if identity in destinations:
                    raise ValueError(f"duplicate output destination: {destination}")
                destinations.add(identity)

    def _outside_cache(self, identity: Path | str) -> None:
        if isinstance(identity, Path):
            inside = self.store.root is not None and identity.is_relative_to(
                self.store.root.resolve()
            )
        else:
            inside = identity == self.store.namespace or identity.startswith(
                self.store.namespace + "/"
            )
        if inside:
            raise ValueError("publication destination must be outside the cache")

    def publish(
        self, artifact: Artifact, destination: str, *, overwrite: bool = False
    ) -> Artifact:
        target = normalize_location(destination)
        identity = location_identity(destination)
        self._outside_cache(identity)
        if file_exists(destination) and not overwrite:
            try:
                existing = self.store.inspect(destination)
            except ValueError:
                existing = None
            if existing is not None and existing.fingerprint == artifact.fingerprint:
                return existing
            raise FileExistsError(f"output destination already exists: {destination}")
        source = self.store.materialize(artifact)
        assert source.path is not None
        directory = self.store.scratch
        if isinstance(target, Path):
            target.parent.mkdir(parents=True, exist_ok=True)
            directory = target.parent
        with tempfile.TemporaryDirectory(
            prefix=".provium-publish-", dir=directory
        ) as staging:
            staged = Path(staging) / "artifact.pa"
            shutil.copyfile(source.path, staged)
            if isinstance(target, Path) and not overwrite:
                os.link(staged, target)
            else:
                publish_file(staged, destination)
        return replace(
            artifact,
            location=identity.as_uri() if isinstance(identity, Path) else identity,
            path=str(identity) if isinstance(identity, Path) else None,
        )
