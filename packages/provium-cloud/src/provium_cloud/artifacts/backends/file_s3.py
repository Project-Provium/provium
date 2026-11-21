"""Local/S3 storage using Provium's artifact verifier and atomic file publisher."""

from typing import Any, Self

from provium.semantic import canonical_hash
from provium_pipeline.publication import FilePublisher
from provium_pipeline.store import Artifact, ArtifactStore
from pydantic import BaseModel, ConfigDict

from provium_cloud.artifacts.interface import ArtifactReference


class FileStoreOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: str
    scratch_directory: str | None = None


class FileStoreBackend:
    def __init__(self, options: FileStoreOptions) -> None:
        self.store = ArtifactStore(options.location, scratch=options.scratch_directory)
        self.namespace = self.store.namespace

    def inspect(self, location: str) -> ArtifactReference:
        artifact = self.store.inspect(location)
        return {
            "location": artifact.location,
            "identity": artifact.identity,
            "fingerprint": artifact.fingerprint,
        }

    def put(self, location: str) -> ArtifactReference:
        reference = self.inspect(location)
        return self.stage(
            canonical_hash(reference["fingerprint"]),
            {"input": location},
            {"input": reference["fingerprint"]["provenance"]},
        )["input"]

    @staticmethod
    def _artifact(reference: ArtifactReference) -> Artifact:
        return Artifact(
            reference["location"], None, reference["identity"], reference["fingerprint"]
        )

    def materialize(self, reference: ArtifactReference) -> str:
        artifact = self.store.materialize(self._artifact(reference))
        assert artifact.path is not None
        return artifact.path

    def stage(
        self, computation: str, paths: dict[str, str], expected: dict[str, str]
    ) -> dict[str, ArtifactReference]:
        return self.store.stage(computation, paths, expected)

    def verify(
        self, outputs: dict[str, ArtifactReference], expected: dict[str, str]
    ) -> None:
        try:
            self.store.verify(outputs, expected)
        except Exception as error:
            response: Any = getattr(error, "response", {})
            if response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                raise FileNotFoundError(
                    "committed artifact no longer exists"
                ) from error
            raise

    def fetch(
        self, reference: ArtifactReference, destination: str, *, overwrite: bool = False
    ) -> None:
        FilePublisher(self.store).publish(
            self._artifact(reference), destination, overwrite=overwrite
        )

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
