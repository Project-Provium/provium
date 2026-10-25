import json
from pathlib import Path

from provium import Artifact, ArtifactReader, ArtifactWriter

from provium_text_pipeline_example.artifact import WORD_COUNT


class WordCountV1ArtifactReader(ArtifactReader):
    def read(self) -> int:
        value = json.loads(self.body.read())
        if type(value) is not int or value < 0:
            raise ValueError("Word count must be a nonnegative integer")
        return value


class WordCountV1ArtifactWriter(ArtifactWriter):
    def write(self, value: int) -> None:
        if type(value) is not int or value < 0:
            raise ValueError("Word count must be a nonnegative integer")
        self.body.write(json.dumps(value).encode())


class WordCountV1Artifact(
    Artifact[WordCountV1ArtifactReader, WordCountV1ArtifactWriter]
):
    definition = WORD_COUNT
    reader = WordCountV1ArtifactReader
    writer = WordCountV1ArtifactWriter

    @classmethod
    def dump(cls, reader: WordCountV1ArtifactReader, path: Path) -> None:
        path.write_text(str(reader.read()) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, writer: WordCountV1ArtifactWriter, path: Path) -> None:
        writer.write(int(path.read_text(encoding="utf-8")))
