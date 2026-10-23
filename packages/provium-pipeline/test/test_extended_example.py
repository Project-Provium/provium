from pathlib import Path

from provium.cli import run

from provium_pipeline.preparation import prepare_pipeline
from provium_pipeline.runtime import ExecutionContext, InputSet
from provium_pipeline.store import ArtifactStore, local_path


def test_extended_example_counts_words_and_reuses_results(tmp_path):
    pipeline = (
        Path(__file__).parents[3] / "examples/provium-text-pipeline/pipeline.yaml"
    )
    source = tmp_path / "document.txt"
    source.write_text("one two one three")
    document = tmp_path / "document.pa"
    assert (
        run(
            [
                "artifact",
                "load",
                "provium_text_pipeline_example.DocumentV1",
                str(source),
                str(document),
            ]
        )
        == 0
    )
    prepared = prepare_pipeline(files=[str(pipeline)])
    with ArtifactStore(str(tmp_path / "store")) as store:
        for expected_reused in (False, True):
            steps = []
            with ExecutionContext(prepared.plan.configuration, store) as execution:
                execution.start(
                    [InputSet({"document": str(document)})],
                    on_step_finished=steps.append,
                )
            assert execution.outcomes[0].state == "completed"
            assert len(steps) == 2
            assert all(step.reused == expected_reused for step in steps)
            output = execution.outcomes[0].outputs["word_count"]
            dumped = tmp_path / "count.txt"
            assert (
                run(["artifact", "dump", str(local_path(output.location)), str(dumped)])
                == 0
            )
            assert int(dumped.read_text()) == 4
