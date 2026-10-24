import csv
from io import StringIO

import pytest
from pipeline_fixtures import COPY
from test_store import make_artifact
from test_target_planning import graph

from provium_pipeline.requests import CSVSource, ExecutionRequest
from provium_pipeline.runtime import ExecutionContext
from provium_pipeline.store import ArtifactStore


def test_csv_rows_select_outputs_and_omit_blank_cells():
    source = CSVSource(
        StringIO(
            "inputs.left,inputs.right,left,nodes.right.result\n"
            "left.pa,,left-out.pa,\n"
            ",right.pa,,right-out.pa\n"
        )
    )
    rows = list(source)
    assert rows[0].inputs == {"left": "left.pa"}
    assert rows[0].destinations == {"left": "left-out.pa"}
    assert rows[1].inputs == {"right": "right.pa"}
    assert rows[1].destinations == {"nodes.right.result": "right-out.pa"}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "inputs.left,left,left\na,b,c\n",
        "inputs.left,left\na,\n",
        "inputs.left,left\na,b,c\n",
        "inputs.left,left\na\n",
    ],
)
def test_csv_rejects_malformed_or_outputless_rows(text):
    with pytest.raises(ValueError):
        list(CSVSource(StringIO(text)))


def test_request_requires_output_and_snapshots_bindings():
    with pytest.raises(ValueError, match="destination"):
        ExecutionRequest({}, {})
    inputs, outputs = {"left": "a.pa"}, {"left": "b.pa"}
    request = ExecutionRequest(inputs, outputs)
    inputs.clear()
    outputs.clear()
    assert request.inputs == {"left": "a.pa"}
    assert request.destinations == {"left": "b.pa"}


@pytest.mark.parametrize("strategy", ["item", "stage"])
def test_different_row_targets_publish_and_reuse_cache(tmp_path, strategy):
    rows = [
        ExecutionRequest(
            {side: str(make_artifact(tmp_path / f"{side}.pa", side))},
            {side: str(tmp_path / f"{side}-out.pa")},
        )
        for side in ("left", "right")
    ]
    with ArtifactStore(str(tmp_path / "cache")) as store:
        for attempt in range(2):
            events = []
            with ExecutionContext(
                graph(),
                store,
                procedures={COPY.identifier: COPY},
                strategy=strategy,
            ) as execution:
                execution.start(rows, on_step_finished=events.append)
            assert [event.state for event in execution.outcomes] == ["completed"] * 2
            assert [event.node for event in events] == ["left", "right"]
            assert execution.worker_starts == (2 if attempt == 0 else 0)
            for side, outcome in zip(("left", "right"), execution.outcomes):
                saved = store.inspect(str(tmp_path / f"{side}-out.pa"))
                assert outcome.outputs[side].location == saved.location
                assert outcome.outputs[side].fingerprint == saved.fingerprint
        assert len(list((store.root / "results").glob("*.json"))) == 2


def test_partial_publication_failure_preserves_cache_and_successes(tmp_path):
    source = make_artifact(tmp_path / "source.pa")
    blocked = tmp_path / "blocked.pa"
    blocked.write_text("keep me")
    request = ExecutionRequest(
        {"left": str(source)},
        {"left": str(tmp_path / "saved.pa"), "last": str(blocked)},
    )
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}
        ) as execution:
            execution.start([request])
        outcome = execution.outcomes[0]
        assert outcome.state == "failed"
        assert set(outcome.outputs) == {"left"}
        assert blocked.read_text() == "keep me"
        assert len(list((store.root / "results").glob("*.json"))) == 2
        blocked.unlink()
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}
        ) as retry:
            retry.start([request])
        assert retry.worker_starts == 0
        assert retry.outcomes[0].state == "completed"


def test_csv_quoted_paths():
    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow(["inputs.left", "left"])
    writer.writerow(["input, one.pa", "output, one.pa"])
    stream.seek(0)
    assert list(CSVSource(stream))[0].destinations["left"] == "output, one.pa"


def test_unused_supplied_input_is_not_read(tmp_path):
    request = ExecutionRequest(
        {"left": str(make_artifact(tmp_path / "source.pa")), "right": "missing.pa"},
        {"left": str(tmp_path / "saved.pa")},
    )
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}
        ) as execution:
            execution.start([request])
        assert execution.outcomes[0].state == "completed"


@pytest.mark.parametrize("collision", ["duplicate", "input", "cache"])
def test_publication_conflicts_fail_before_computation(tmp_path, collision):
    source = make_artifact(tmp_path / "source.pa")
    saved = tmp_path / "saved.pa"
    if collision == "input":
        saved = source
    elif collision == "cache":
        saved = tmp_path / "cache" / "saved.pa"
    rows = [ExecutionRequest({"left": str(source)}, {"left": str(saved)})]
    if collision == "duplicate":
        rows.append(ExecutionRequest({"left": str(source)}, {"last": str(saved)}))
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with pytest.raises(RuntimeError, match="executor failed"):
            with ExecutionContext(
                graph(), store, procedures={COPY.identifier: COPY}
            ) as execution:
                execution.start(rows)
        assert execution.worker_starts == 0
        assert source.exists()
        assert not list((store.root / "results").glob("*.json"))


def test_cli_csv_and_required_output(tmp_path, capsys, monkeypatch):
    import json

    from provium.cli import run
    from test_commands import config

    monkeypatch.chdir(tmp_path)
    pipeline = config(tmp_path)
    source = make_artifact(tmp_path / "source.pa")
    common = ["pipeline", "run", "-f", pipeline, "--store", str(tmp_path / "cache")]
    assert run([*common, "--input", f"document={source}"]) == 2
    assert "requires at least one --output" in capsys.readouterr().err
    batch = tmp_path / "requests.csv"
    batch.write_text(f"inputs.document,tokens\n{source},saved.pa\n")
    assert run([*common, "--csv", str(batch), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)[0]
    assert result["state"] == "completed"
    assert result["outputs"]["tokens"]["location"] == (tmp_path / "saved.pa").as_uri()
    assert run([*common, "--csv", str(batch), "--input", f"document={source}"]) == 2
    assert "--csv cannot be combined" in capsys.readouterr().err


def test_custom_publisher_is_used(tmp_path):
    from dataclasses import replace

    class MemoryPublisher:
        def validate(self, requests):
            assert len(requests) == 1

        def publish(self, artifact, destination, *, overwrite=False):
            assert destination == "memory:result"
            assert not overwrite
            return replace(artifact, location=destination, path=None)

    request = ExecutionRequest(
        {"left": str(make_artifact(tmp_path / "source.pa"))},
        {"left": "memory:result"},
    )
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with ExecutionContext(
            graph(),
            store,
            procedures={COPY.identifier: COPY},
            publisher=MemoryPublisher(),
        ) as execution:
            execution.start([request])
        assert execution.outcomes[0].outputs["left"].location == "memory:result"


def test_csv_requires_output_column():
    with pytest.raises(ValueError, match="destination column"):
        list(CSVSource(StringIO("inputs.left\na.pa\n")))


def test_filtered_required_procedure_is_a_failed_request(tmp_path):
    request = ExecutionRequest(
        {"left": str(make_artifact(tmp_path / "source.pa"))},
        {"left": str(tmp_path / "saved.pa")},
    )
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with ExecutionContext(
            graph(),
            store,
            procedures={COPY.identifier: COPY},
            only=["nodes.right"],
        ) as execution:
            execution.start([request])
        assert execution.outcomes[0].state == "failed"
        assert "required procedures may be filtered out" in execution.outcomes[0].error
        assert execution.worker_starts == 0


def test_legacy_queue_rejects_publication_requests(tmp_path):
    from types import SimpleNamespace

    request = ExecutionRequest({"left": "unused.pa"}, {"left": "saved.pa"})
    backend = SimpleNamespace(definition=lambda name: None)
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with ExecutionContext(
            graph(),
            store,
            procedures={COPY.identifier: COPY},
            backend=backend,
        ) as execution:
            report = execution.inspect_inputs([request])[0]
        assert report["state"] == "failed"
        assert "output destinations require direct requests" in report["error"]


def test_public_request_and_publication_exports():
    from provium_pipeline import (
        CSVSource as PublicCSVSource,
    )
    from provium_pipeline import (
        ExecutionRequest as PublicExecutionRequest,
    )
    from provium_pipeline import (
        FilePublisher,
        Publisher,
        RequestSource,
    )

    assert PublicCSVSource is CSVSource
    assert PublicExecutionRequest is ExecutionRequest
    assert FilePublisher and Publisher and RequestSource


def test_failed_copy_preserves_cache_and_previous_output(tmp_path, monkeypatch):
    import errno

    import provium_pipeline.publication as module

    source = make_artifact(tmp_path / "source.pa")
    destination = make_artifact(tmp_path / "destination.pa", "previous")
    previous = destination.read_bytes()
    with ArtifactStore(str(tmp_path / "cache")) as store:
        artifact = store.inspect(str(source))

        def interrupted(source_path, staged_path):
            staged_path.write_bytes(b"partial")
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(module.shutil, "copyfile", interrupted)
        with pytest.raises(OSError) as error:
            module.FilePublisher(store).publish(
                artifact, str(destination), overwrite=True
            )
        assert error.value.errno == errno.ENOSPC
        assert destination.read_bytes() == previous
        assert source.exists()
        assert not list(tmp_path.glob(".provium-publish-*"))


def test_request_inspection_uses_selected_targets_without_publishing(tmp_path):
    request = ExecutionRequest(
        {"left": str(make_artifact(tmp_path / "source.pa"))},
        {"left": str(tmp_path / "saved.pa")},
    )
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}
        ) as execution:
            execution.start([request])
        (tmp_path / "saved.pa").unlink()
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}
        ) as inspection:
            report = inspection.inspect_inputs([request])[0]
        assert report["state"] == "completed"
        assert set(report["nodes"]) == {"left"}
        assert set(report["outputs"]) == {"left"}
        assert not (tmp_path / "saved.pa").exists()
