"""Validated graph documents and independent execution settings."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from provium.files import local_file
from pydantic import BaseModel, ConfigDict, Field, field_validator

NAME = re.compile(r"^[^.$\s/]+$")
REFERENCE = re.compile(r"^\$(inputs\.[^.$\s/]+|nodes\.[^.$\s/]+\.[^.$\s/]+)$")
SELECTOR = re.compile(
    r"^(default|tags\.[^.$\s/]+|pipelines\.[^.$\s/]+\.(tags|nodes)\.[^.$\s/]+)$"
)
Binding = str | list[str] | None


def name_check(value: str) -> str:
    if not NAME.fullmatch(value):
        raise ValueError(f"invalid name: {value!r}")
    return value


def refs(value: Binding) -> list[str]:
    return [] if value is None else [value] if isinstance(value, str) else value


def bindings_check(value: dict[str, Binding]) -> dict[str, Binding]:
    for name, binding in value.items():
        name_check(name)
        for ref in refs(binding):
            if not REFERENCE.fullmatch(ref):
                raise ValueError(f"invalid artifact reference: {ref}")
    return value


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str

    _name = field_validator("name")(name_check)


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    procedure: str
    configuration: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Binding] = Field(default_factory=dict)
    setup_inputs: dict[str, Binding] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    _bindings = field_validator("inputs", "setup_inputs")(bindings_check)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str]) -> list[str]:
        return [name_check(value) for value in values]


class PipelineConfiguration(Document):
    kind: Literal["PipelineConfiguration"] = "PipelineConfiguration"
    nodes: dict[str, Node]
    outputs: dict[str, str]

    @field_validator("nodes")
    @classmethod
    def validate_nodes(cls, value: dict[str, Node]) -> dict[str, Node]:
        for name in value:
            name_check(name)
        return value

    @field_validator("outputs")
    @classmethod
    def validate_outputs(cls, value: dict[str, str]) -> dict[str, str]:
        bindings_check(dict(value))
        return value

    def order(self) -> list[str]:
        dependencies: dict[str, set[str]] = {}
        for name, node in self.nodes.items():
            dependencies[name] = {
                ref.split(".")[1]
                for binding in (*node.inputs.values(), *node.setup_inputs.values())
                for ref in refs(binding)
                if ref.startswith("$nodes.")
            }
            if dependencies[name] - self.nodes.keys():
                raise ValueError(f"unknown dependency at node {name}")
        for ref in self.outputs.values():
            if ref.startswith("$nodes.") and ref.split(".")[1] not in self.nodes:
                raise ValueError(f"unknown output source: {ref}")
        ordered: list[str] = []
        while len(ordered) < len(self.nodes):
            ready = next(
                (
                    name
                    for name in self.nodes
                    if name not in ordered and dependencies[name] <= set(ordered)
                ),
                None,
            )
            if ready is None:
                raise ValueError("pipeline contains a cycle")
            ordered.append(ready)
        used: set[str] = set()

        def visit(name: str) -> None:
            if name not in used:
                used.add(name)
                for dependency in dependencies[name]:
                    visit(dependency)

        for ref in self.outputs.values():
            if ref.startswith("$nodes."):
                visit(ref.split(".")[1])
        if used != set(self.nodes):
            raise ValueError(f"unused nodes: {sorted(self.nodes.keys() - used)}")
        return ordered


@dataclass(frozen=True)
class PipelineDefinition:
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    profiles: frozenset[str] = field(default_factory=frozenset[str])

    def snapshot(self) -> dict[str, Any]:
        return {
            "inputs": {
                name: value if isinstance(value, str) else value.identifier
                for name, value in self.inputs.items()
            },
            "outputs": {
                name: value if isinstance(value, str) else value.identifier
                for name, value in self.outputs.items()
            },
            "profiles": sorted(self.profiles),
        }

    @classmethod
    def from_snapshot(cls, value: dict[str, Any]) -> PipelineDefinition:
        return cls(
            dict(value["inputs"]), dict(value["outputs"]), frozenset(value["profiles"])
        )


@dataclass(frozen=True)
class Settings:
    workers: int = 1
    soft: float | None = None
    hard: float | None = None


def duration(value: Any) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("time limit must be positive finite seconds")
    return float(value)


def specificity(selector: str, pipeline: str, node: str, tags: Iterable[str]) -> int:
    parts = selector.split(".")
    if parts == ["default"]:
        return 0
    if parts[0] == "pipelines":
        if parts[1] != pipeline:
            return -1
        parts = parts[2:]
    if parts[0] == "tags" and parts[1] in tags:
        return 1
    if parts[0] == "nodes" and parts[1] == node:
        return 2
    return -1


class ComputeProfile(Document):
    kind: Literal["ComputeProfile"] = "ComputeProfile"
    workers: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    soft_time_limits: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    hard_time_limits: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    soft_time_limit: float | None = None
    hard_time_limit: float | None = None

    _duration = field_validator("soft_time_limit", "hard_time_limit", mode="before")(
        duration
    )

    @field_validator("workers", "soft_time_limits", "hard_time_limits")
    @classmethod
    def validate_rules(
        cls, rules: list[dict[str, Any]], info: Any
    ) -> list[dict[str, Any]]:
        for rule in rules:
            if len(rule) != 1:
                raise ValueError("each rule must have exactly one selector")
            selector, value = next(iter(rule.items()))
            if not SELECTOR.fullmatch(selector):
                raise ValueError(f"invalid selector: {selector}")
            if info.field_name == "workers":
                if type(value) is not int or value < 1:
                    raise ValueError("workers must be a positive integer")
            else:
                duration(value)
        return rules

    def settings(
        self,
        pipeline: str,
        node: str,
        tags: Iterable[str],
        overrides: list[dict[str, Any]] | None = None,
    ) -> Settings:
        def resolve(rules: list[dict[str, Any]], default: Any) -> Any:
            rank = -1
            result = default
            for rule in rules:
                selector, value = next(iter(rule.items()))
                level = specificity(selector, pipeline, node, tags)
                if level >= 0 and level >= rank:
                    rank, result = level, value
            return result

        return Settings(
            resolve(self.workers + (overrides or []), 1),
            resolve(self.soft_time_limits, None),
            resolve(self.hard_time_limits, None),
        )


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
