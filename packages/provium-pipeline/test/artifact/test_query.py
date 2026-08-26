from provium_pipeline.artifact.query import (
    ArtifactQuery,
    ArtifactQueryService,
    RunArtifactBinding,
)


def binding(
    identity: str,
    *,
    run_id: str = "run-1",
    record_key: str = "record-1",
    node_id: str = "node-1",
    output_field: str = "result",
    disposition: str = "produced",
    origin_run_id: str | None = None,
    origin_task_id: str | None = None,
) -> RunArtifactBinding:
    return RunArtifactBinding(
        artifact_identity=identity,
        run_id=run_id,
        record_key=record_key,
        node_id=node_id,
        output_field=output_field,
        disposition=disposition,
        origin_run_id=origin_run_id or run_id,
        origin_task_id=origin_task_id or f"task-{identity}",
    )


def test_query_filters_run_artifacts_and_orders_results() -> None:
    service = ArtifactQueryService(
        bindings=(
            binding("z", node_id="other"),
            binding("b"),
            binding("a"),
            binding("ignored", run_id="run-2"),
        ),
        location_lookup=lambda identity: (f"store://{identity}",),
    )

    results = service.query(
        ArtifactQuery(
            run_id="run-1",
            record_key="record-1",
            node_id="node-1",
            output_field="result",
        )
    )

    assert [result.binding.artifact_identity for result in results] == ["a", "b"]
    assert all(result.locations == () for result in results)


def test_query_can_include_locations_and_preserves_reuse_provenance() -> None:
    reused = binding(
        "reused",
        disposition="reused",
        origin_run_id="run-origin",
        origin_task_id="task-origin",
    )
    service = ArtifactQueryService(
        bindings=(reused,),
        location_lookup=lambda identity: (f"store://{identity}", "cache://copy"),
    )

    (result,) = service.query(ArtifactQuery(run_id="run-1", include_locations=True))

    assert result.binding.disposition == "reused"
    assert result.binding.origin_run_id == "run-origin"
    assert result.binding.origin_task_id == "task-origin"
    assert result.locations == ("cache://copy", "store://reused")
