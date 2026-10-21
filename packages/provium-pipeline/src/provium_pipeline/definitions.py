"""Discover pipeline definitions from explicit references or entry points."""

import importlib
from importlib.metadata import entry_points

from .models import PipelineDefinition


def load_definition(name: str, reference: str | None) -> PipelineDefinition | None:
    if reference:
        module, separator, attribute = reference.partition(":")
        if not separator:
            raise ValueError("definition must be module:attribute")
        value = getattr(importlib.import_module(module), attribute)
    else:
        matches = list(entry_points(group="provium.pipeline.definitions", name=name))
        if len(matches) > 1:
            raise ValueError(f"duplicate pipeline definition: {name}")
        if not matches:
            return None
        value = matches[0].load()
    if not isinstance(value, PipelineDefinition):
        raise TypeError("expected PipelineDefinition")
    return value
