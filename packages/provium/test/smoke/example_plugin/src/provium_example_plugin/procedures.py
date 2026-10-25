"""Concrete installed-plugin procedure implementations."""

from provium import Procedure, ProcedureProcessContext, ProcedureSetupContext
from provium_example_plugin.contracts import (
    FAILING_PROCEDURE,
    SOURCE_PROCEDURE,
    TRANSFORM_PROCEDURE,
    FailingContract,
    SourceConfig,
    SourceContract,
    TransformConfig,
    TransformContract,
)
from provium_example_plugin.import_probe import record_import

record_import("procedures")


class SourceProcedure(
    Procedure[
        SourceConfig,
        SourceContract.SetupInputs,
        SourceContract.Inputs,
        SourceContract.Outputs,
    ]
):
    definition = SOURCE_PROCEDURE

    def process(self, context, configuration, inputs, outputs):
        with outputs.value.open() as writer:
            writer.write_text(configuration.text)


class TransformProcedure(
    Procedure[
        TransformConfig,
        TransformContract.SetupInputs,
        TransformContract.Inputs,
        TransformContract.Outputs,
    ]
):
    definition = TRANSFORM_PROCEDURE

    def setup(
        self,
        context: ProcedureSetupContext,
        configuration: TransformConfig,
        inputs: TransformContract.SetupInputs,
    ) -> None:
        with inputs.setup.open() as reader:
            self.setup_text = reader.read_text()

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: TransformConfig,
        inputs: TransformContract.Inputs,
        outputs: TransformContract.Outputs,
    ) -> None:
        parts = [self.setup_text]
        with inputs.required.open() as reader:
            parts.append(reader.read_text())
        if inputs.optional is not None:
            with inputs.optional.open() as reader:
                parts.append(reader.read_text())
        for binding in inputs.repeated:
            with binding.open() as reader:
                parts.append(reader.read_text())
        body = f"{configuration.prefix}{''.join(parts)}{configuration.suffix}"
        with outputs.transformed.open() as writer:
            writer.write_text(body)
        with outputs.summary.open() as writer:
            writer.write_text(f"inputs={len(parts)};characters={len(body)}")


class FailingProcedure(
    Procedure[
        None,
        FailingContract.SetupInputs,
        FailingContract.Inputs,
        FailingContract.Outputs,
    ]
):
    definition = FAILING_PROCEDURE

    def process(self, context, configuration, inputs, outputs):
        with inputs.source.open() as reader:
            partial = reader.read_text()[:2]
        with outputs.result.open() as writer:
            writer.write_text(partial)
        raise RuntimeError("deliberate installed-plugin failure")
