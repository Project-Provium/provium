from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import cast

from provium import JsonValue
from provium_pipeline import InputRecordKey
from provium_pipeline.artifact.index import ArtifactIndex
from provium_pipeline.definition.models import PipelineDefinition
from provium_pipeline.input.models import InputRecord, RunInputSnapshot
from provium_pipeline.input.resolver import InputRecordResolverCatalog
from provium_pipeline.resolved_run_creation import ResolvedRunCreationService
from provium_pipeline.run_models import PipelineRun


def test_resolved_run_creation_freezes_resolver_records_and_persists_run() -> None:
    pipeline_definition = cast(PipelineDefinition, object())
    created = cast(PipelineRun, object())

    class Resolver:
        identifier = "example.ready-records"

        def resolve(self, request: object, context: object) -> tuple[InputRecord, ...]:
            assert getattr(request, "configuration") == {"minimum_age": 30}
            assert getattr(request, "shared_inputs") == {"lexicon": ("artifact-1",)}
            assert getattr(context, "artifact_index") is artifact_index
            return (
                InputRecord(
                    key=InputRecordKey("record-1"),
                    inputs={},
                    labels={"state": "ready"},
                ),
            )

    class Runs:
        snapshot: RunInputSnapshot | None = None

        def create_from_snapshot(
            self,
            definition: PipelineDefinition,
            *,
            input_snapshot: RunInputSnapshot,
            metadata: Mapping[str, JsonValue] | None = None,
        ) -> PipelineRun:
            assert definition is pipeline_definition
            assert metadata == {"selection": {"all": True}}
            self.snapshot = input_snapshot
            return created

    artifact_index = cast(ArtifactIndex, SimpleNamespace())
    catalog = InputRecordResolverCatalog()
    catalog.register(Resolver())
    runs = Runs()
    service = ResolvedRunCreationService(
        resolvers=catalog,
        artifact_index=artifact_index,
        runs=runs,
    )

    result = service.create(
        pipeline_definition,
        resolver_identifier="example.ready-records",
        configuration={"minimum_age": 30},
        shared_inputs={"lexicon": ("artifact-1",)},
        metadata={"selection": {"all": True}},
    )

    assert result is created
    assert runs.snapshot is not None
    assert [str(record.key) for record in runs.snapshot.records] == ["record-1"]
    assert runs.snapshot.sources[0].identifier == "example.ready-records"
    assert runs.snapshot.sources[0].configuration == {"minimum_age": 30}
