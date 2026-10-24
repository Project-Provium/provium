"""Load local and remote YAML pipeline documents."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import yaml
from provium.files import local_file

from .models import PipelineConfiguration


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def load_documents(
    paths: Iterable[str | Path],
    *,
    scratch_directory: str | Path | None = None,
) -> list[PipelineConfiguration]:
    documents: list[PipelineConfiguration] = []
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
