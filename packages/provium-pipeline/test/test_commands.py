import json

from provium.cli import run
from provium_text_pipeline_example.procedure.tokenize.definition import TOKENIZE
from test_store import make_artifact


def config(tmp_path):
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "kind: PipelineConfiguration\nname: tokenize\n"
        "inputs: {document: provium_text_pipeline_example.DocumentV1}\nnodes:\n"
        f"  tokenize:\n    procedure: {TOKENIZE.identifier}\n"
        '    inputs:\n      source: "$inputs.document"\n'
        'outputs:\n  tokens: "$nodes.tokenize.destination"\n'
    )
    return str(path)


def test_direct_cli_and_read_only_status(tmp_path, capsys):
    source = make_artifact(tmp_path / "source.pa")
    args = [
        "-f",
        config(tmp_path),
        "--input",
        f"document={source}",
        "--store",
        str(tmp_path / "store"),
    ]
    assert (
        run(
            [
                "pipeline",
                "run",
                *args,
                "--output",
                f"tokens={tmp_path / 'tokens.pa'}",
                "--json",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["state"] == "completed"
    assert "run=" in captured.err
    assert "Run log:" not in captured.err
    assert run(["pipeline", "status", *args, "--outputs", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value[0]["state"] == "completed"
    assert value[0]["outputs"]["tokens"]["location"]


def test_item_failure_and_bad_configuration_have_distinct_exit_codes(tmp_path):
    assert (
        run(
            [
                "pipeline",
                "run",
                "--output",
                f"tokens={tmp_path / 'tokens.pa'}",
                "-f",
                config(tmp_path),
                "--input",
                "document=missing.pa",
                "--store",
                str(tmp_path / "store"),
            ]
        )
        == 1
    )
    assert (
        run(
            [
                "pipeline",
                "run",
                "--output",
                f"tokens={tmp_path / 'tokens.pa'}",
                "-f",
                config(tmp_path),
                "--workers",
                "default=0",
                "--store",
                str(tmp_path / "store"),
            ]
        )
        != 0
    )


def test_missing_input_reports_error_and_releases_writer_lock(tmp_path, capsys):
    from provium_pipeline.store import ArtifactStore

    source = tmp_path / "source.pa"
    store_path = tmp_path / "store"
    args = [
        "pipeline",
        "run",
        "--output",
        f"tokens={tmp_path / 'tokens.pa'}",
        "-f",
        config(tmp_path),
        "--input",
        f"document={source}",
        "--store",
        str(store_path),
        "--json",
    ]
    assert run(args) == 1
    captured = capsys.readouterr()
    outcome = json.loads(captured.out)[0]
    assert outcome["state"] == "failed"
    assert "file not found" in outcome["error"]
    assert str(source) in outcome["error"]
    assert "file not found" in captured.err
    assert str(source) in captured.err
    assert "Run log:" not in captured.err
    with ArtifactStore(str(store_path)) as store, store.writer_lock():
        pass
    make_artifact(source)
    assert run(args) == 0
    assert json.loads(capsys.readouterr().out)[0]["state"] == "completed"


def test_run_defaults_to_readable_output(tmp_path, capsys):
    source = tmp_path / "source.pa"
    args = [
        "pipeline",
        "run",
        "--output",
        f"tokens={tmp_path / 'tokens.pa'}",
        "-f",
        config(tmp_path),
        "--input",
        f"document={source}",
        "--store",
        str(tmp_path / "store"),
    ]
    assert run(args) == 1
    captured = capsys.readouterr()
    assert captured.out.startswith("Input set 1: failed\n")
    assert "file not found" in captured.out
    assert str(source) in captured.out
    make_artifact(source)
    assert run(args) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Input set 1: completed\n")
    assert "tokens: file://" in captured.out
    assert "run=" in captured.err


def test_verbose_reports_cached_skips_without_polluting_json(tmp_path, capsys):
    source = make_artifact(tmp_path / "source.pa")
    args = [
        "pipeline",
        "run",
        "--output",
        f"tokens={tmp_path / 'tokens.pa'}",
        "-f",
        config(tmp_path),
        "--input",
        f"document={source}",
        "--store",
        str(tmp_path / "store"),
        "--json",
    ]
    assert run([*args, "-v"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["state"] == "completed"
    assert "Skipped" not in captured.err
    assert run(args) == 0
    assert "Skipped" not in capsys.readouterr().err
    for flag in ("-v", "--verbose"):
        assert run([*args, flag]) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)[0]["state"] == "completed"
        assert "Skipped tokenize: reused cached outputs" in captured.err
    assert run([*args, "-v", "--force", "all"]) == 0
    assert "Skipped" not in capsys.readouterr().err


def test_core_cli_plugin_remains_loadable():
    from provium.cli import discover_command_catalogs

    assert "pipeline" in discover_command_catalogs().commands
