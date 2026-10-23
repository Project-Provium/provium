from __future__ import annotations

from typing import TYPE_CHECKING

from provium import ArtifactCatalog, ArtifactDefinition

_path = "provium_text_pipeline_example"

if TYPE_CHECKING:
    from .document import DocumentV1Artifact
    from .tokens import TokensV1Artifact
    from .word_count import WordCountV1Artifact

DOCUMENT: ArtifactDefinition[DocumentV1Artifact] = ArtifactDefinition(
    identifier=f"{_path}.DocumentV1",
    target=f"{_path}.artifact.document:DocumentV1Artifact",
    description="An artifact containing a text document",
)

TOKENS: ArtifactDefinition[TokensV1Artifact] = ArtifactDefinition(
    identifier=f"{_path}.TokensV1",
    target=f"{_path}.artifact.tokens:TokensV1Artifact",
    description="An artifact containing word tokens",
)


WORD_COUNT: ArtifactDefinition[WordCountV1Artifact] = ArtifactDefinition(
    identifier=f"{_path}.WordCountV1",
    target=f"{_path}.artifact.word_count:WordCountV1Artifact",
    description="An artifact containing the total word count",
)

catalog = ArtifactCatalog()
catalog.register(DOCUMENT)
catalog.register(TOKENS)
catalog.register(WORD_COUNT)
