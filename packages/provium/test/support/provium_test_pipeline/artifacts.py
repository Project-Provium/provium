"""Concrete artifact implementation for the integration test pipeline."""

from provium import Artifact, ArtifactReader, ArtifactWriter
from support.provium_test_pipeline.contracts import TEXT_ARTIFACT
from support.provium_test_pipeline.import_probe import record_implementation_import

record_implementation_import("artifacts")


class TextReader(ArtifactReader):
    """Read the complete artifact body as bytes or UTF-8 text."""

    def read_bytes(self) -> bytes:
        return self.body.read()

    def read_text(self) -> str:
        return self.read_bytes().decode("utf-8")


class TextWriter(ArtifactWriter):
    """Write bytes or UTF-8 text to an artifact body."""

    def write_bytes(self, value: bytes) -> int:
        return self.body.write(value)

    def write_text(self, value: str) -> int:
        return self.write_bytes(value.encode("utf-8"))


class TextArtifact(Artifact[TextReader, TextWriter]):
    """A disk-backed UTF-8 text artifact."""

    definition = TEXT_ARTIFACT
    reader = TextReader
    writer = TextWriter
