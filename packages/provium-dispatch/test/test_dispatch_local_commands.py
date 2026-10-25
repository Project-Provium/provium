"""Direct dispatch execution preserves the shared CLI behavior."""

import json

import pytest
from provium.cli import run
from test_commands import config
from test_store import make_artifact


@pytest.mark.parametrize("group", ["pipeline", "dispatch"])
def test_empty_csv_does_not_start_work(tmp_path, capsys, group):
    csv = tmp_path / "requests.csv"
    csv.write_text("inputs.document,tokens\n")
    assert (
        run(
            [
                group,
                "run",
                "-f",
                config(tmp_path),
                "--csv",
                str(csv),
                "--store",
                str(tmp_path / "cache"),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "No input sets processed.\n"


def test_dispatch_direct_execution_and_cached_verbose(tmp_path, capsys):
    source = make_artifact(tmp_path / "source.pa")
    common = ["-f", config(tmp_path), "--store", str(tmp_path / "cache")]
    inputs = ["--input", f"document={source}"]
    outputs = ["--output", f"tokens={tmp_path / 'tokens.pa'}"]
    assert (
        run(
            [
                "dispatch",
                "run",
                *common,
                *inputs,
                *outputs,
                "-v",
                "--workers",
                "default=1",
            ]
        )
        == 0
    )
    assert "tokens: file://" in capsys.readouterr().out
    assert run(["dispatch", "run", *common, *inputs, *outputs, "-v", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["state"] == "completed"
    assert "Skipped tokenize" in captured.err
    assert run(["dispatch", "status", *common, *inputs]) == 0
    assert json.loads(capsys.readouterr().out)[0]["state"] == "completed"
    assert run(["dispatch", "inspect", *common]) == 0
    assert json.loads(capsys.readouterr().out)["order"] == ["tokenize"]
    assert (
        run(["dispatch", "run", *common, "--input", "document=missing", *outputs]) == 1
    )
    assert "file not found" in capsys.readouterr().out


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--workers", "broken"],
        ["--idle-wait", "nan"],
        ["--input", "broken"],
        ["--csv", "unused", "--input", "document=unused"],
    ],
)
def test_dispatch_invalid_local_request_does_not_create_store(tmp_path, options):
    cache = tmp_path / "cache"
    assert (
        run(
            ["dispatch", "run", "-f", config(tmp_path), "--store", str(cache), *options]
        )
        == 2
    )
    assert not cache.exists()


def test_dispatch_inspection_requires_a_graph():
    assert run(["dispatch", "inspect"]) == 2


@pytest.mark.parametrize(
    "text",
    [
        "---\n",
        "- invalid\n",
        "kind: Unknown\n",
        "kind: ComputeProfile\nname: same\n---\nkind: ComputeProfile\nname: same\n",
    ],
)
def test_dispatch_document_validation(tmp_path, text):
    from provium_dispatch.documents import load_documents

    path = tmp_path / "documents.yaml"
    path.write_text(text)
    if text == "---\n":
        assert load_documents([path]) == []
    else:
        with pytest.raises((ValueError, TypeError)):
            load_documents([path])


def test_dispatch_profile_allowlist_and_queued_targets(tmp_path):
    from unittest.mock import Mock

    from provium_pipeline import ArtifactStore
    from provium_text_pipeline_example.artifact import DOCUMENT, TOKENS
    from test_runtime import configuration

    from provium_dispatch import ComputeProfile, ExecutionContext, PipelineDefinition

    definition = PipelineDefinition(
        {"document": DOCUMENT}, {"tokens": TOKENS}, frozenset({"allowed"})
    )
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with ExecutionContext(
            configuration(),
            store,
            definition=definition,
            profile=ComputeProfile(name="allowed"),
        ):
            pass
        backend = Mock()
        backend.definition.return_value = None
        with pytest.raises(ValueError, match="target selection"):
            ExecutionContext(
                configuration(), store, backend=backend, targets=["tokens"]
            )
