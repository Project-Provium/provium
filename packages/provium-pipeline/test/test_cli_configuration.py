"""Pipeline CLI settings are resolved only for execution."""

from pathlib import Path

import pytest
from provium.cli.application import create_parser
from provium.cli.catalog import CommandCatalog

from provium_pipeline.cli.arguments import require_pipeline
from provium_pipeline.cli.commands.run import RunCommand


def parse(*arguments: str):
    catalog = CommandCatalog()
    catalog.register(RunCommand)
    return create_parser(catalog).parse_args(["run", *arguments])


def test_project_defaults_and_explicit_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PROVIUM_DATABASE_URL", raising=False)
    monkeypatch.delenv("PROVIUM_PIPELINE_STORE", raising=False)
    config = tmp_path / ".provium"
    config.mkdir()
    (config / "config.toml").write_text(
        '[pipeline]\ndatabase = "postgresql://configured"\n'
        'store = "configured-store"\nscratch_directory = "scratch"\n'
    )
    args = parse("registered")
    require_pipeline(args)
    assert args.database == "postgresql://configured"
    assert args.store == "configured-store"
    assert args.scratch_directory == "scratch"
    (config / "config.toml").write_text("invalid after first load")
    require_pipeline(args)
    assert args.database == "postgresql://configured"
    (config / "config.toml").write_text('[pipeline]\ndatabase = "configured"\n')
    args = parse("registered", "--database", "explicit", "--store", "explicit-store")
    require_pipeline(args)
    assert args.database == "explicit"
    assert args.store == "explicit-store"
    monkeypatch.setenv("PROVIUM_DATABASE_URL", "environment")
    monkeypatch.setenv("PROVIUM_PIPELINE_STORE", "environment-store")
    args = parse("registered")
    require_pipeline(args)
    assert args.database == "environment"
    assert args.store == "environment-store"
    monkeypatch.setenv("PROVIUM_CONFIG__PIPELINE__STORE", "generic-environment")
    args = parse("registered", "--config", "pipeline.database=override")
    require_pipeline(args)
    assert args.database == "override"
    assert args.store == "generic-environment"
    args = parse("registered", "--config", "pipeline.store=override-store")
    require_pipeline(args)
    assert args.store == "override-store"


def test_invalid_syntax_and_help_do_not_load_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".provium"
    config.mkdir()
    (config / "config.toml").write_text("invalid toml")
    for argv in [("--help",), ("--unknown",)]:
        with pytest.raises(SystemExit):
            parse(*argv)


@pytest.mark.parametrize("text", ["pipeline = 1", "[pipeline]\ndatabase = 1"])
def test_invalid_settings_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / ".provium"
    config.mkdir()
    (config / "config.toml").write_text(text)
    with pytest.raises((TypeError, ValueError), match="pipeline"):
        require_pipeline(parse("-f", "graph.yaml"))
