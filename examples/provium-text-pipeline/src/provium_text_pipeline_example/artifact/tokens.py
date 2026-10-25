import json
from pathlib import Path

from provium import Artifact, ArtifactReader, ArtifactWriter

from provium_text_pipeline_example.artifact import TOKENS


class TokensV1ArtifactReader(ArtifactReader):
    def read(self) -> list[str]:
        values = json.loads(self.body.read())
        if not isinstance(values, list):
            raise TypeError("Invalid data.")
        return values


class TokensV1ArtifactWriter(ArtifactWriter):
    def write(self, values: list[str]):
        self.body.write(json.dumps(values).encode())


class TokensV1Artifact(Artifact[TokensV1ArtifactReader, TokensV1ArtifactWriter]):
    definition = TOKENS
    reader = TokensV1ArtifactReader
    writer = TokensV1ArtifactWriter

    @classmethod
    def dump(cls, reader: TokensV1ArtifactReader, path: Path):
        values = reader.read()
        path.write_text("\n".join(values))

    @classmethod
    def load(cls, writer: TokensV1ArtifactWriter, path: Path):
        values = list(map(str.strip, path.read_text().split("\n")))
        writer.write(values)
