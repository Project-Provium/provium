"""Importable procedures for isolated worker lifecycle tests."""

import time

from provium import (
    Procedure,
    ProcedureConfig,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureInputs,
    ProcedureOutputs,
    input_field,
    output,
)
from provium_text_pipeline_example.artifact import DOCUMENT


class Configuration(ProcedureConfig):
    delay: float = 0
    fail: bool = False


class ValueContract(ProcedureContract[Configuration]):
    configuration = Configuration

    class SetupInputs(ProcedureInputs):
        scale = input_field(float)

    class Inputs(ProcedureInputs):
        count = input_field(int)
        optional = input_field(int, optional=True)

    class Outputs(ProcedureOutputs):
        result = output(DOCUMENT)


class Values(
    Procedure[
        Configuration,
        ValueContract.SetupInputs,
        ValueContract.Inputs,
        ValueContract.Outputs,
    ]
):
    def setup(self, context, configuration, inputs):
        self.scale = inputs.scale

    def process(self, context, configuration, inputs, outputs):
        time.sleep(configuration.delay)
        if configuration.fail:
            raise ValueError("deliberate procedure failure")
        with outputs.result.open() as writer:
            writer.write(str(inputs.count * self.scale))


VALUES = ProcedureDefinition(
    "cloud.tests.Values", "procedures:Values", "Values", None, ValueContract
)
Values.definition = VALUES


class EmptyContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        pass

    class Inputs(ProcedureInputs):
        pass

    class Outputs(ProcedureOutputs):
        pass


class Empty(
    Procedure[
        None, EmptyContract.SetupInputs, EmptyContract.Inputs, EmptyContract.Outputs
    ]
):
    def process(self, context, configuration, inputs, outputs):
        pass


EMPTY = ProcedureDefinition(
    "cloud.tests.Empty", "procedures:Empty", "Empty", None, EmptyContract
)
Empty.definition = EMPTY


class OptionalContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        pass

    class Inputs(ProcedureInputs):
        source = input_field(DOCUMENT, optional=True)

    class Outputs(ProcedureOutputs):
        result = output(DOCUMENT)


class Optional(
    Procedure[
        None,
        OptionalContract.SetupInputs,
        OptionalContract.Inputs,
        OptionalContract.Outputs,
    ]
):
    def process(self, context, configuration, inputs, outputs):
        with outputs.result.open() as writer:
            writer.write("empty")


OPTIONAL = ProcedureDefinition(
    "cloud.tests.Optional", "procedures:Optional", "Optional", None, OptionalContract
)
Optional.definition = OPTIONAL
