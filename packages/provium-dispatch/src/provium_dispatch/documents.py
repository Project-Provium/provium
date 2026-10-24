"""Load local and remote YAML pipeline documents."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import yaml
from provium.files import local_file
from provium_pipeline.documents import UniqueLoader
from provium_pipeline.models import PipelineConfiguration

from .models import ComputeProfile


def load_documents(
    paths: Iterable[str | Path],
    *,
    scratch_directory: str | Path | None = None,
) -> list[PipelineConfiguration | ComputeProfile]:
    documents: list[PipelineConfiguration | ComputeProfile] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        with local_file(path, scratch_directory=scratch_directory) as local:
            for value in yaml.load_all(
                local.read_text(encoding="utf-8"), Loader=UniqueLoader
            ):
                if value is None:
                    continue
                if not isinstance(value, dict):
                    raise TypeError("document must be a mapping")
                kind = cast(dict[str, Any], value).get("kind", "")
                model = {
                    "PipelineConfiguration": PipelineConfiguration,
                    "ComputeProfile": ComputeProfile,
                }.get(kind)
                if model is None:
                    raise ValueError(f"unknown document kind: {kind}")
                document = model.model_validate(value)
                key = (document.kind, document.name)
                if key in seen:
                    raise ValueError(f"duplicate document: {key}")
                seen.add(key)
                documents.append(document)
    return documents
