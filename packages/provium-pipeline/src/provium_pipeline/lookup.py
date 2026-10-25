"""Resolve cached output references using manifests, without executing procedures."""

from __future__ import annotations

from typing import Any, Protocol, cast

from provium.semantic import computation_key, output_key

from provium_pipeline.models import refs
from provium_pipeline.planning import Plan
from provium_pipeline.store import Artifact, ArtifactStore


class ResultReader(Protocol):
    def result(self, namespace: str, key: str) -> dict[str, Any] | None: ...


def split_selector(selector: str) -> tuple[str, str]:
    for marker in (".nodes.", ".outputs."):
        if marker in selector:
            pipeline, reference = selector.rsplit(marker, 1)
            parts = reference.split(".")
            if (
                pipeline
                and all(parts)
                and len(parts) == (2 if marker == ".nodes." else 1)
            ):
                return pipeline, marker[1:] + reference
    raise ValueError("expected pipeline.nodes.node.output or pipeline.outputs.output")


def lookup_artifact(
    plan: Plan,
    store: ArtifactStore,
    inputs: dict[str, Any],
    selector: str,
    *,
    backend: ResultReader | None = None,
) -> Artifact:
    pipeline, reference = split_selector(selector)
    if pipeline != plan.configuration.name:
        raise ValueError(f"selector pipeline does not match {plan.configuration.name}")
    if inputs.keys() != plan.input_types.keys():
        raise ValueError("input names do not match pipeline")
    values = store.restore(
        inputs,
        {name: item["fingerprint"]["provenance"] for name, item in inputs.items()},
    )
    for name, artifact in values.items():
        if artifact.fingerprint["type"] != plan.input_types[name]:
            raise ValueError(f"incompatible pipeline input: {name}")
    values = {f"$inputs.{name}": artifact for name, artifact in values.items()}
    if reference.startswith("outputs."):
        alias = reference[len("outputs.") :]
        if alias not in plan.configuration.outputs:
            raise ValueError(f"unknown pipeline output: {alias}")
        reference = plan.configuration.outputs[alias]
    else:
        reference = "$" + reference

    needed: set[str] = set()
    pending = [reference]
    while pending:
        ref = pending.pop()
        if ref in values:
            continue
        parts = ref.split(".")
        if len(parts) != 3 or parts[0] != "$nodes":
            raise ValueError(f"unknown output reference: {ref}")
        _, name, output = parts
        if name not in plan.output_types or output not in plan.output_types[name]:
            raise ValueError(f"unknown output reference: {ref}")
        if name in needed:
            continue
        needed.add(name)
        node = plan.configuration.nodes[name]
        for bindings in (node.inputs, node.setup_inputs):
            for value in bindings.values():
                pending.extend(refs(value))

    for name in plan.order:
        if name not in needed:
            continue
        node = plan.configuration.nodes[name]
        contract = plan.procedures[name].resolve_contract()

        def bind(scope: str) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for field in getattr(contract.metadata, scope):
                value = getattr(node, scope).get(
                    field.name, [] if field.repeated else None
                )
                result[field.name] = (
                    None
                    if value is None
                    else [values[item].fingerprint for item in cast(list[str], value)]
                    if isinstance(value, list)
                    else values[value].fingerprint
                )
            return result

        key = computation_key(plan.records[name], bind("inputs"), bind("setup_inputs"))
        manifest = (
            backend.result(store.namespace, key) if backend else store.manifest(key)
        )
        if manifest is None:
            raise ValueError(f"no cached result for node {name!r} (computation {key})")
        expected = {
            output: output_key(key, output, kind)
            for output, kind in plan.output_types[name].items()
        }
        restored = store.restore(manifest, expected)
        for output_name, artifact in restored.items():
            if artifact.fingerprint["type"] != plan.output_types[name][output_name]:
                raise ValueError(f"incompatible cached output: {name}.{output_name}")
            values[f"$nodes.{name}.{output_name}"] = artifact
    return values[reference]
