"""Concrete procedure implementations for the integration test pipeline."""

from __future__ import annotations

from threading import Event
from typing import ClassVar

from provium import Procedure, ProcedureProcessContext, ProcedureSetupContext
from support.provium_test_pipeline.contracts import (
    FAILING_PROCEDURE,
    SOURCE_PROCEDURE,
    TRANSFORM_PROCEDURE,
    FailingContract,
    SourceConfig,
    SourceContract,
    TransformConfig,
    TransformContract,
)
from support.provium_test_pipeline.import_probe import record_implementation_import

record_implementation_import("procedures")


class SourceProcedure(
    Procedure[
        SourceConfig,
        SourceContract.SetupInputs,
        SourceContract.Inputs,
        SourceContract.Outputs,
    ]
):
    """Create a configured seed artifact."""

    definition = SOURCE_PROCEDURE

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: SourceConfig,
        inputs: SourceContract.Inputs,
        outputs: SourceContract.Outputs,
    ) -> None:
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
    """Statefully combine all supplied text artifacts."""

    definition = TRANSFORM_PROCEDURE
    instances: ClassVar[list[TransformProcedure]] = []
    process_started_event: ClassVar[Event | None] = None
    process_continue_event: ClassVar[Event | None] = None

    def __init__(self) -> None:
        self.setup_text = ""
        self.setup_calls = 0
        self.process_calls = 0
        self.close_calls = 0
        self.setup_context: ProcedureSetupContext
        self.setup_configuration: TransformConfig
        self.process_contexts: list[ProcedureProcessContext] = []
        self.process_configurations: list[TransformConfig] = []
        self.setup_temporary_directory_existed = False
        self.process_temporary_directories_existed: list[bool] = []
        self.setup_temporary_directory_existed_during_close = False
        type(self).instances.append(self)

    def setup(
        self,
        context: ProcedureSetupContext,
        configuration: TransformConfig,
        inputs: TransformContract.SetupInputs,
    ) -> None:
        self.setup_calls += 1
        self.setup_context = context
        self.setup_configuration = configuration
        self.setup_temporary_directory_existed = context.temporary_directory.exists()
        with inputs.setup.open() as reader:
            self.setup_text = reader.read_text()

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: TransformConfig,
        inputs: TransformContract.Inputs,
        outputs: TransformContract.Outputs,
    ) -> None:
        self.process_calls += 1
        self.process_contexts.append(context)
        self.process_configurations.append(configuration)
        self.process_temporary_directories_existed.append(
            context.temporary_directory.exists()
        )
        if type(self).process_started_event is not None:
            type(self).process_started_event.set()
            assert type(self).process_continue_event is not None
            type(self).process_continue_event.wait()
            context.cancellation.raise_if_cancelled()
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

    def close(self) -> None:
        self.close_calls += 1
        self.setup_temporary_directory_existed_during_close = (
            self.setup_context.temporary_directory.exists()
        )


class FailingProcedure(
    Procedure[
        None,
        FailingContract.SetupInputs,
        FailingContract.Inputs,
        FailingContract.Outputs,
    ]
):
    """Write partial output before raising a predictable processing error."""

    definition = FAILING_PROCEDURE
    instances: ClassVar[list[FailingProcedure]] = []

    def __init__(self) -> None:
        self.should_fail = True
        type(self).instances.append(self)

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: None,
        inputs: FailingContract.Inputs,
        outputs: FailingContract.Outputs,
    ) -> None:
        with inputs.source.open() as reader:
            partial = reader.read_text()[:3]
        with outputs.result.open() as writer:
            writer.write_text(partial)
        if outputs.secondary is not None:
            with outputs.secondary.open() as writer:
                writer.write_text("partial-secondary")
        if self.should_fail:
            raise RuntimeError("deliberate integration test failure")
