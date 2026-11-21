"""Validate invocation input sets before durable admission."""

from typing import Any

from provium.procedure.discovery import discover_procedure_catalogs
from provium_pipeline.values import encode_value

from provium_cloud.artifacts import ArtifactBackend
from provium_cloud.invocations.models import InvocationPlan


def prepare_inputs(
    plan: InvocationPlan,
    inputs: dict[str, Any],
    artifacts: ArtifactBackend,
) -> dict[str, Any]:
    if inputs.keys() - plan.graph.inputs.keys():
        raise ValueError("unknown invocation inputs")
    result: dict[str, Any] = {}
    for name, declaration in plan.graph.inputs.items():
        value = inputs.get(name)
        if isinstance(declaration, dict):
            result[name] = encode_value(declaration, value)
        elif value is None:
            result[name] = None
        else:
            reference = artifacts.inspect(str(value))
            if reference["fingerprint"]["type"] != declaration:
                raise ValueError(f"incompatible input type: {name}")
            result[name] = reference
    for reference in plan.targets.values():
        if reference.startswith("$inputs.") and result[reference[8:]] is None:
            raise ValueError(f"missing required output input: {reference[8:]}")
    catalog = discover_procedure_catalogs()
    for task in plan.tasks.values():
        contract = catalog.resolve(task.procedure).resolve_contract()
        for scope in ("inputs", "setup_inputs"):
            for field in getattr(contract.metadata, scope):
                reference = getattr(task, scope).get(field.name)
                if isinstance(reference, str) and reference.startswith("$inputs."):
                    value: Any = result[reference[8:]]
                    absent = (
                        value is None
                        or field.value_type is not None
                        and value["value_input"]["value"] is None
                    )
                    if absent and not field.optional:
                        raise ValueError(f"missing required input: {reference[8:]}")
    for name, declaration in plan.graph.inputs.items():
        if isinstance(declaration, str) and result[name] is not None:
            result[name] = artifacts.put(str(inputs[name]))
    return result
