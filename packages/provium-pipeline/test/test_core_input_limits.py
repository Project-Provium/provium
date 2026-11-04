"""Pipelines deliberately accept only single artifact fields for now."""

from types import new_class

import pytest
from provium import (
    ProcedureContract,
    ProcedureDefinition,
    ProcedureInputs,
    input_field,
    multi_input_field,
    repeated_input,
)
from provium_text_pipeline_example.artifact import DOCUMENT

from provium_pipeline.models import PipelineConfiguration
from provium_pipeline.planning import build_plan


@pytest.mark.parametrize("scope", ["Inputs", "SetupInputs"])
@pytest.mark.parametrize(
    "declaration",
    [
        lambda: multi_input_field(DOCUMENT),
        lambda: multi_input_field(DOCUMENT, maximum=1, optional=True),
        lambda: repeated_input(DOCUMENT),
        lambda: input_field(int, optional=True),
    ],
)
def test_pipeline_rejects_unsupported_core_fields(scope, declaration):
    inputs = type("Inputs", (ProcedureInputs,), {"source": declaration()})
    contract = new_class(
        "Contract",
        (ProcedureContract[None],),
        exec_body=lambda ns: ns.update({scope: inputs}),
    )
    definition = ProcedureDefinition(
        "tests.Limited",
        "tests:Limited",
        "Limited",
        None,
        contract,
    )
    graph = PipelineConfiguration(
        name="limited",
        inputs={},
        outputs={"result": "$nodes.step.result"},
        nodes={"step": {"procedure": definition.identifier}},
    )
    with pytest.raises(ValueError, match="single artifact"):
        build_plan(graph, procedures={definition.identifier: definition})
