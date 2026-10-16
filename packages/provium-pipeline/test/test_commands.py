import json

from provium_text_pipeline_example.procedure.tokenize.definition import TOKENIZE
from test_store import make_artifact

from provium_pipeline.cli import run


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
    assert run(["run", *args]) == 0
    capsys.readouterr()
    assert run(["status", *args, "--outputs", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value[0]["state"] == "completed"
    assert value[0]["outputs"]["tokens"]["location"]


def test_item_failure_zero_but_bad_configuration_nonzero(tmp_path):
    assert (
        run(
            [
                "run",
                "-f",
                config(tmp_path),
                "--input",
                "document=missing.pa",
                "--store",
                str(tmp_path / "store"),
            ]
        )
        == 0
    )
    assert (
        run(
            [
                "run",
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


def test_core_cli_plugin_remains_loadable():
    from provium.cli import discover_command_catalogs

    assert "pipeline" in discover_command_catalogs().commands
