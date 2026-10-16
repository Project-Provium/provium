"""Direct and PostgreSQL-backed pipeline command line."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from dataclasses import asdict
from importlib.metadata import entry_points
from typing import Any

from provium.cli import CLI_PLUGIN_API_VERSION, CLIPlugin, Command, CommandCatalog

from .database import PostgresBackend
from .models import (
    ComputeProfile,
    PipelineConfiguration,
    PipelineDefinition,
    load_documents,
)
from .planning import build_plan
from .runtime import ExecutionContext, InputSet, Outcome
from .store import ArtifactStore


def configure(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("list", "queue"):
        child = subparsers.add_parser(action)
        if action == "list":
            child.add_argument(
                "resource", nargs="?", choices=("pipelines", "profiles"), default=None
            )
        else:
            child.add_argument("pipeline", nargs="?")
        child.add_argument("--database", default=os.environ.get("PROVIUM_DATABASE_URL"))
        child.add_argument("--json", action="store_true")
    for action in (
        "unregister",
        "run",
        "register",
        "enqueue",
        "status",
        "validate",
        "inspect",
        "retry",
    ):
        child = subparsers.add_parser(action)
        child.add_argument("pipeline", nargs="?")
        child.add_argument("-f", "--file", action="append", default=[])
        child.add_argument("--database", default=os.environ.get("PROVIUM_DATABASE_URL"))
        child.add_argument(
            "--store",
            default=os.environ.get("PROVIUM_PIPELINE_STORE") or "./.provium/artifacts",
        )
        child.add_argument("--scratch-directory")
        child.add_argument(
            "--definition",
            help="Python module:attribute containing a PipelineDefinition",
        )
        child.add_argument("--input", action="append", default=[])
        child.add_argument("--profile")
        child.add_argument("--only", action="append", default=[])
        child.add_argument("--workers", action="append", default=[])
        child.add_argument("--force", action="append", default=[])
        child.add_argument("--idle-wait", type=float, default=0)
        child.add_argument("--outputs", action="store_true")
        child.add_argument("--json", action="store_true")


def assignments(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item or key in result:
            raise ValueError(f"invalid or duplicate binding: {value}")
        result[key] = item
    return result


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


def outcome_json(event: Outcome) -> dict[str, Any]:
    return {
        "state": event.state,
        "node": event.node,
        "reused": event.reused,
        "outputs": {
            name: {"location": artifact.location, **artifact.fingerprint}
            for name, artifact in event.outputs.items()
        },
        "error": event.error,
    }


def execute(arguments: argparse.Namespace) -> int:
    action = arguments.action
    if action in ("list", "queue"):
        if not arguments.database:
            raise ValueError(
                "database required; use --database or PROVIUM_DATABASE_URL"
            )
        backend = PostgresBackend(arguments.database)
        if action == "list":
            kind = {
                "pipelines": "PipelineConfiguration",
                "profiles": "ComputeProfile",
            }.get(arguments.resource)
            print(json.dumps(backend.registered(kind)))
        else:
            print(json.dumps(backend.queue_info(arguments.pipeline)))
        return 0
    documents = load_documents(arguments.file)
    graphs = [doc for doc in documents if isinstance(doc, PipelineConfiguration)]
    profiles = {doc.name: doc for doc in documents if isinstance(doc, ComputeProfile)}
    backend = None
    if action in ("register", "unregister", "enqueue", "retry") or arguments.pipeline:
        if not arguments.database:
            raise ValueError(
                "database required; use --database or PROVIUM_DATABASE_URL"
            )
        backend = PostgresBackend(arguments.database)
    if action == "unregister":
        if documents and (arguments.pipeline or arguments.profile):
            raise ValueError("unregister accepts files or explicit names, not both")
        identities = [{"kind": doc.kind, "name": doc.name} for doc in documents]
        if arguments.pipeline:
            identities.append(
                {"kind": "PipelineConfiguration", "name": arguments.pipeline}
            )
        if arguments.profile:
            identities.append({"kind": "ComputeProfile", "name": arguments.profile})
        if not identities:
            raise ValueError("unregister requires files, a pipeline name or --profile")
        assert backend is not None
        print(json.dumps(backend.unregister(identities)))
        return 0
    if action == "register":
        if not documents:
            raise ValueError("register requires YAML documents")
        for graph in graphs:
            build_plan(graph, load_definition(graph.name, arguments.definition))
        assert backend is not None
        backend.register(
            [doc.model_dump(mode="json") for doc in documents],
            definitions={
                graph.name: definition
                for graph in graphs
                if (definition := load_definition(graph.name, arguments.definition))
                is not None
            },
        )
        print(json.dumps({"registered": len(documents)}))
        return 0
    if arguments.pipeline:
        if documents or action == "run" and arguments.input:
            raise ValueError(
                "registered execution cannot combine files or direct inputs"
            )
        assert backend is not None
        graph = PipelineConfiguration.model_validate(
            backend.get("PipelineConfiguration", arguments.pipeline)
        )
    else:
        if len(graphs) != 1:
            raise ValueError("select exactly one pipeline with -f or a registered name")
        graph = graphs[0]
    definition = load_definition(graph.name, arguments.definition)
    if action == "retry":
        assert backend is not None
        print(json.dumps({"reset": backend.retry(graph.name)}))
        return 0
    if action == "enqueue":
        assert backend is not None
        values = assignments(arguments.input)
        print(json.dumps({"id": backend.enqueue(graph.name, values)}))
        return 0
    profile = None
    if arguments.profile:
        if backend:
            profile = ComputeProfile.model_validate(
                backend.get("ComputeProfile", arguments.profile)
            )
        elif arguments.profile in profiles:
            profile = profiles[arguments.profile]
        else:
            raise ValueError(f"unknown profile: {arguments.profile}")
    overrides: list[dict[str, Any]] = []
    for value in arguments.workers:
        key, separator, count = value.partition("=")
        if not separator:
            raise ValueError("worker override requires selector=count")
        overrides.append({key: int(count)})
    store = ArtifactStore(arguments.store, scratch=arguments.scratch_directory)
    with (
        store,
        ExecutionContext(
            graph,
            store,
            definition=definition,
            profile=profile,
            workers=overrides,
            only=arguments.only,
            force=arguments.force,
            backend=backend,
            idle_wait=arguments.idle_wait,
        ) as context,
    ):
        if action in ("validate", "inspect"):
            print(
                json.dumps(
                    {
                        "pipeline": graph.name,
                        "order": context.plan.order,
                        "settings": {
                            name: asdict(settings)
                            for name, settings in context.settings.items()
                        },
                    }
                )
            )
        elif action == "status":
            if backend and not arguments.input:
                rows: list[dict[str, Any]] = []
                after = 0
                while batch := backend.pending(
                    graph.name, after, include_finished=True
                ):
                    rows.extend(batch)
                    after = batch[-1]["id"]
                if arguments.outputs:
                    result: list[dict[str, Any]] = []
                    for row in rows:
                        recorded_store = row["detail"].get("store", store.namespace)
                        with ArtifactStore(
                            recorded_store, scratch=arguments.scratch_directory
                        ) as reader:
                            with ExecutionContext(
                                graph, reader, backend=backend, only=arguments.only
                            ) as inspector:
                                report = inspector.inspect_inputs(
                                    [InputSet(row["inputs"])]
                                )[0]
                                report["id"] = row["id"]
                                result.append(report)
                else:
                    result = [
                        {"id": row["id"], "state": row["state"], **row["detail"]}
                        for row in rows
                    ]
            else:
                result = context.inspect_inputs(
                    [InputSet(assignments(arguments.input))]
                )
            print(json.dumps(result))
        elif action == "run":
            items = [] if backend else [InputSet(assignments(arguments.input))]
            context.start(items)
        else:
            raise ValueError(f"unsupported action: {action}")
    if action == "run":
        print(json.dumps([outcome_json(event) for event in context.outcomes]))
        print(f"Run log: {context.log_path}", file=sys.stderr)
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="provium-pipeline")
    configure(parser)
    arguments = parser.parse_args(argv)
    try:
        return execute(arguments)
    except Exception as error:
        print(f"provium-pipeline: {error}", file=sys.stderr)
        return 2


def main() -> None:
    raise SystemExit(run())


class PipelineCommand(Command):
    name = "pipeline"
    help = "Execute and manage provenance-addressed procedure graphs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure(parser)

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            return execute(arguments)
        except Exception as error:
            print(f"provium pipeline: {error}", file=sys.stderr)
            return 2


catalog = CommandCatalog()
catalog.register(PipelineCommand)
plugin = CLIPlugin(CLI_PLUGIN_API_VERSION, "provium-pipeline", catalog)
