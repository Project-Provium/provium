from pathlib import Path

from provium import Artifact, ArtifactReader, ArtifactWriter

from provium_text_pipeline_example.artifact import DOCUMENT


class DocumentV1ArtifactReader(ArtifactReader):
    def read(self) -> str:
        return self.body.read().decode()


class DocumentV1ArtifactWriter(ArtifactWriter):
    def write(self, value: str):
        self.body.write(bytes(value.encode()))


class DocumentV1Artifact(Artifact[DocumentV1ArtifactReader, DocumentV1ArtifactWriter]):
    definition = DOCUMENT
    reader = DocumentV1ArtifactReader
    writer = DocumentV1ArtifactWriter

    @classmethod
    def dump(cls, reader: DocumentV1ArtifactReader, path: Path) -> None:
        if path.exists():
            raise FileExistsError("The file `{path}` already exists.")
        if not path.name.endswith(".txt"):
            path = Path(str(path) + ".txt")
        path.write_text(reader.read())

    @classmethod
    def load(cls, writer: DocumentV1ArtifactWriter, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"The file `{path}` does not exist.")
        if not path.name.endswith(".txt"):
            raise ValueError("Input files must .txt format.")
        writer.write(path.read_text())
