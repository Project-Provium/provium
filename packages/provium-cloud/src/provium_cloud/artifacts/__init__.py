"""Artifact backend selection and extension discovery."""

from importlib.metadata import entry_points
from typing import cast
from urllib.parse import urlparse

from provium_cloud.artifacts.interface import ArtifactBackend, ArtifactReference
from provium_cloud.deployment import BackendConfiguration


def open_artifacts(config: BackendConfiguration) -> ArtifactBackend:
    if config.backend in {"file", "s3"}:
        from provium_cloud.artifacts.backends.file_s3 import (
            FileStoreBackend,
            FileStoreOptions,
        )

        options = FileStoreOptions.model_validate(config.options)
        scheme = urlparse(options.location).scheme
        if (config.backend == "s3" and scheme != "s3") or (
            config.backend == "file" and scheme not in {"", "file"}
        ):
            raise ValueError("artifact location does not match its backend")
        return FileStoreBackend(options)
    factories = list(
        entry_points(group="provium.cloud.artifact_backends", name=config.backend)
    )
    if len(factories) != 1:
        raise ValueError(f"unknown or ambiguous artifact backend: {config.backend}")
    return cast(ArtifactBackend, factories[0].load()(dict(config.options)))


__all__ = ["ArtifactBackend", "ArtifactReference", "open_artifacts"]
