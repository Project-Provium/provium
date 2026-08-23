from __future__ import annotations

from provium_pipeline import (
    InputRecord,
    InputResolutionContext,
    InputSourceKind,
    ResolveInputRecordsRequest,
    resolve_input_snapshot,
)
from provium_pipeline.artifact import InMemoryArtifactIndex
from provium_pipeline.identifiers import InputRecordKey


class Resolver:
    identifier = "test.resolver"

    def __init__(self) -> None:
        self.calls = 0

    def resolve(
        self,
        request: ResolveInputRecordsRequest,
        context: InputResolutionContext,
    ) -> tuple[InputRecord, ...]:
        del request, context
        self.calls += 1
        return (
            InputRecord(
                key=InputRecordKey("record-1"),
                inputs={"image": ("artifact-1",)},
                labels={},
            ),
        )


def test_resolver_result_is_frozen_with_replay_metadata() -> None:
    resolver = Resolver()
    request = ResolveInputRecordsRequest(
        configuration={"partition": "new"},
        shared_inputs={"model": ("model-1",)},
    )
    context = InputResolutionContext(artifact_index=InMemoryArtifactIndex())

    snapshot = resolve_input_snapshot(resolver, request=request, context=context)

    assert resolver.calls == 1
    assert snapshot.records[0].key == InputRecordKey("record-1")
    assert snapshot.shared_inputs == {"model": ("model-1",)}
    assert len(snapshot.sources) == 1
    source = snapshot.sources[0]
    assert source.kind is InputSourceKind.RESOLVER
    assert source.identifier == "test.resolver"
    assert source.configuration == {"partition": "new"}
    assert source.result_digest == snapshot.digest
