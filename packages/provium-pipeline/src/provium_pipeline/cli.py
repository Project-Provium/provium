"""Provium CLI plugin: pipeline validate and pipeline run."""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from provium import (
    ArtifactDefinition,
    load_json_configuration,
    load_yaml_configuration,
)
from provium.cli import CLI_PLUGIN_API_VERSION, CLIPlugin, Command, CommandCatalog

from .executor import PipelineExecutor
from .models import Pipeline


def _assignments(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition("=")
        if not separator or not name or not content:
            raise ValueError(f"expected NAME=VALUE: {value!r}")
        if name in result:
            raise ValueError(f"duplicate assignment: {name!r}")
        result[name] = content
    return result


def _configuration(arguments: argparse.Namespace) -> list[Mapping[str, object]]:
    layers: list[Mapping[str, object]] = []
    for filename in arguments.config_file:
        path = Path(filename)
        loader = (
            load_json_configuration
            if path.suffix == ".json"
            else load_yaml_configuration
        )
        layers.append(loader(path))
    overrides: dict[str, object] = {}
    for name, value in _assignments(arguments.config).items():
        try:
            overrides[name] = json.loads(value)
        except json.JSONDecodeError:
            overrides[name] = value
    layers.append(overrides)
    return layers


def _paths(
    values: Sequence[str],
    definitions: Mapping[str, ArtifactDefinition[Any]],
) -> dict[str, Path]:
    paths = _assignments(values)
    unknown = paths.keys() - definitions.keys()
    if unknown:
        raise ValueError(f"unknown pipeline bindings: {sorted(unknown)}")
    return {name: Path(value) for name, value in paths.items()}


class PipelineCommand(Command):
    """Load, validate and execute a pipeline definition."""

    name = "pipeline"
    help = "Validate or execute a YAML pipeline"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        subparsers = parser.add_subparsers(dest="pipeline_action", required=True)
        for action in ("validate", "run"):
            command = subparsers.add_parser(action)
            command.add_argument("definition")
            command.add_argument(
                "--config", action="append", default=[], metavar="NAME=VALUE"
            )
            command.add_argument(
                "--config-file", action="append", default=[], metavar="PATH"
            )
            if action == "run":
                command.add_argument(
                    "--input", action="append", default=[], metavar="NAME=PATH"
                )
                command.add_argument(
                    "--output", action="append", default=[], metavar="NAME=PATH"
                )
                command.add_argument("--keep-intermediates", metavar="DIRECTORY")

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            pipeline = Pipeline.from_yaml(arguments.definition)
            executor = PipelineExecutor()
            layers = _configuration(arguments)
            plan = executor.plan(pipeline, configuration_layers=layers)
            if arguments.pipeline_action == "validate":
                print(f"{plan.name}: " + " -> ".join(step.name for step in plan.steps))
                return 0
            steps = {step.name: step for step in plan.steps}
            output_types: dict[str, ArtifactDefinition[Any]] = {}
            for name, reference in plan.outputs.items():
                assert reference.step is not None
                output_types[name] = steps[reference.step].outputs[reference.name]
            inputs = {
                name: plan.inputs[name].resolve().bind_read(path)
                for name, path in _paths(arguments.input, plan.inputs).items()
            }
            outputs = {
                name: output_types[name].resolve().bind_write(path)
                for name, path in _paths(arguments.output, output_types).items()
            }
            result = executor.execute(
                pipeline,
                configuration_layers=layers,
                inputs=inputs,
                outputs=outputs,
                keep_intermediates=arguments.keep_intermediates,
            )
        except Exception as error:
            print(f"pipeline: {error}", file=sys.stderr)
            return 1
        print(result.identity)
        if result.intermediate_directory is not None:
            print(f"Intermediates: {result.intermediate_directory}")
        return 0


catalog = CommandCatalog()
catalog.register(PipelineCommand)
plugin = CLIPlugin(CLI_PLUGIN_API_VERSION, "provium-pipeline", catalog)

__all__ = ["PipelineCommand", "plugin"]
