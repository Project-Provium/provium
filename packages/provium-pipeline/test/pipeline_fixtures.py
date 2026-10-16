import os
import time

from provium import (
    Procedure,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureInputs,
    ProcedureOutputs,
    output,
)
from provium import input as input_field
from provium.procedure.io import optional_input, repeated_input
from provium_text_pipeline_example.artifact import DOCUMENT


class SourceContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        pass

    class Inputs(ProcedureInputs):
        pass

    class Outputs(ProcedureOutputs):
        result = output(DOCUMENT)


SOURCE = ProcedureDefinition(
    identifier="pipeline.tests.Source",
    target="pipeline_fixtures:Source",
    label="Source",
    description=None,
    contract=SourceContract,
)
SLOW = ProcedureDefinition(
    identifier="pipeline.tests.Slow",
    target="pipeline_fixtures:Slow",
    label="Slow",
    description=None,
    contract=SourceContract,
)


class Source(
    Procedure[
        None, SourceContract.SetupInputs, SourceContract.Inputs, SourceContract.Outputs
    ]
):
    definition = SOURCE

    def process(self, context, configuration, inputs, outputs):
        with outputs.result.open() as writer:
            writer.write(str(os.getpid()))


class Slow(
    Procedure[
        None, SourceContract.SetupInputs, SourceContract.Inputs, SourceContract.Outputs
    ]
):
    definition = SLOW

    def process(self, context, configuration, inputs, outputs):
        time.sleep(2)
        with outputs.result.open() as writer:
            writer.write("finished")


class CopyContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        pass

    class Inputs(ProcedureInputs):
        source = input_field(DOCUMENT)

    class Outputs(ProcedureOutputs):
        result = output(DOCUMENT)


COPY = ProcedureDefinition(
    identifier="pipeline.tests.Copy",
    target="pipeline_fixtures:Copy",
    label="Copy",
    description=None,
    contract=CopyContract,
)


class Copy(
    Procedure[None, CopyContract.SetupInputs, CopyContract.Inputs, CopyContract.Outputs]
):
    definition = COPY

    def process(self, context, configuration, inputs, outputs):
        with inputs.source.open() as reader:
            text = reader.read()
        if text.startswith("bad"):
            raise ValueError("deliberate input error")
        if text.startswith("slow"):
            time.sleep(0.5)
        with outputs.result.open() as writer:
            writer.write(text)


class JoinContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        weights = input_field(DOCUMENT)

    class Inputs(ProcedureInputs):
        left = input_field(DOCUMENT)
        right = input_field(DOCUMENT)

    class Outputs(ProcedureOutputs):
        result = output(DOCUMENT)
        mirror = output(DOCUMENT)


JOIN = ProcedureDefinition(
    identifier="pipeline.tests.Join",
    target="pipeline_fixtures:Join",
    label="Join",
    description=None,
    contract=JoinContract,
)


class Join(
    Procedure[None, JoinContract.SetupInputs, JoinContract.Inputs, JoinContract.Outputs]
):
    definition = JOIN

    def process(self, context, configuration, inputs, outputs):
        with inputs.left.open() as left, inputs.right.open() as right:
            text = left.read() + right.read()
        with outputs.result.open() as result, outputs.mirror.open() as mirror:
            result.write(text)
            mirror.write(text)


class CollectionContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        optional = optional_input(DOCUMENT)

    class Inputs(ProcedureInputs):
        sources = repeated_input(DOCUMENT)
        optional = optional_input(DOCUMENT)

    class Outputs(ProcedureOutputs):
        result = output(DOCUMENT)


COLLECTION = ProcedureDefinition(
    identifier="pipeline.tests.Collection",
    target="pipeline_fixtures:Collection",
    label="Collection",
    description=None,
    contract=CollectionContract,
)


class Collection(
    Procedure[
        None,
        CollectionContract.SetupInputs,
        CollectionContract.Inputs,
        CollectionContract.Outputs,
    ]
):
    definition = COLLECTION

    def process(self, context, configuration, inputs, outputs):
        text = ""
        for source in inputs.sources:
            with source.open() as reader:
                text += reader.read()
        with outputs.result.open() as writer:
            writer.write(text)
