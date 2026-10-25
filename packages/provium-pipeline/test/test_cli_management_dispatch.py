"""Read-only command validation."""

import json

import pytest
from provium.cli import run


@pytest.mark.parametrize("command", ["inspect", "status"])
@pytest.mark.parametrize("idle_wait", ["-1", "nan", "inf"])
def test_invalid_idle_wait_reports_error(command, idle_wait, capsys):
    assert run(["pipeline", command, "-f", "unused", "--idle-wait", idle_wait]) == 2
    assert "idle wait must be finite and nonnegative" in capsys.readouterr().err


@pytest.mark.parametrize("selector", ["default", "nodes.tokenize"])
def test_inspect_resolves_worker_overrides(tmp_path, capsys, selector):
    from test_commands import config

    assert (
        run(
            [
                "pipeline",
                "inspect",
                "-f",
                config(tmp_path),
                "--workers",
                f"{selector}=2",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["settings"]["tokenize"]["workers"] == 2
