"""Concrete installed-plugin artifact implementation."""

from provium import Artifact, ArtifactReader, ArtifactWriter
from provium_example_plugin.contracts import TEXT_ARTIFACT
from provium_example_plugin.import_probe import record_import

record_import("artifacts")


class TextReader(ArtifactReader):
    def read_text(self) -> str:
        return self.body.read().decode("utf-8")


class TextWriter(ArtifactWriter):
    def write_text(self, value: str) -> int:
        return self.body.write(value.encode("utf-8"))


class TextArtifact(Artifact[TextReader, TextWriter]):
    definition = TEXT_ARTIFACT
    reader = TextReader
    writer = TextWriter
