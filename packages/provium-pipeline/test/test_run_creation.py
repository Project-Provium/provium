from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from provium_pipeline.artifact.index import ArtifactIndex
from provium_pipeline.compiler.models import CompiledPipeline
from provium_pipeline.definition.models import PipelineDefinition
from provium_pipeline.identifiers import InputRecordKey, InputSetIdentifier
from provium_pipeline.inputs import InputRecord, InputSet, InputSourceKind
from provium_pipeline.run_creation import RunCreationService
from provium_pipeline.run_models import CreateRunRequest, PipelineRun


def test_run_creation_compiles_freezes_validates_and_persists_named_input_set() -> None:
    pipeline_definition = cast(PipelineDefinition, object())
    compiled = cast(CompiledPipeline, SimpleNamespace(inputs=()))
    input_set = InputSet(
        identity="input-set-digest",
        identifier=InputSetIdentifier("evaluation-v1"),
        records=(
            InputRecord(
                key=InputRecordKey("record-1"),
                inputs={},
                labels={"split": "evaluation"},
            ),
        ),
        digest="input-set-digest",
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
        metadata={},
    )
    created = cast(PipelineRun, object())

    class Compiler:
        def compile(self, definition: PipelineDefinition) -> CompiledPipeline:
            assert definition is pipeline_definition
            return compiled

    class InputSets:
        def get(self, identity: str | InputSetIdentifier) -> InputSet:
            assert identity == "evaluation-v1"
            return input_set

    class Runs:
        request: CreateRunRequest | None = None

        def create_run(self, request: CreateRunRequest) -> PipelineRun:
            self.request = request
            return created

    runs = Runs()
    service = RunCreationService(
        compiler=Compiler(),
        input_sets=InputSets(),
        artifact_index=cast(ArtifactIndex, SimpleNamespace()),
        runs=runs,
    )

    result = service.create(
        pipeline_definition,
        input_set="evaluation-v1",
        metadata={"selection": "all"},
    )

    assert result is created
    assert runs.request is not None
    assert runs.request.pipeline is compiled
    assert runs.request.inputs.records == input_set.records
    assert runs.request.inputs.sources[0].kind is InputSourceKind.INPUT_SET
    assert runs.request.inputs.sources[0].identifier == input_set.identity
    assert runs.request.inputs.sources[0].result_digest == input_set.digest
    assert runs.request.metadata == {"selection": "all"}
