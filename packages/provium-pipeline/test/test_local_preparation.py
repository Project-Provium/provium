"""Local selection and settings never require a registry."""

import pytest
from provium.cli.application import create_parser
from provium.cli.catalog import CommandCatalog

from provium_pipeline.cli.arguments import assert_pipeline_is_selected
from provium_pipeline.cli.commands.run import RunCommand
from provium_pipeline.preparation import load_pipeline


def parse(*arguments):
    catalog = CommandCatalog()
    catalog.register(RunCommand)
    return create_parser(catalog).parse_args(["run", *arguments])


def test_local_settings_precedence_and_single_load(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    directory = tmp_path / ".provium"
    directory.mkdir()
    path = directory / "config.toml"
    path.write_text('[pipeline]\nstore = "configured"\nscratch_directory = "scratch"\n')
    args = parse("-f", "graph.yaml")
    assert_pipeline_is_selected(args)
    assert (args.store, args.scratch_directory) == ("configured", "scratch")
    path.write_text("invalid after first load")
    assert_pipeline_is_selected(args)
    assert args.store == "configured"
    path.write_text("[pipeline]\n")
    monkeypatch.delenv("PROVIUM_PIPELINE_STORE", raising=False)
    args = parse("-f", "graph.yaml")
    assert_pipeline_is_selected(args)
    assert args.store == "./.provium/artifacts"


@pytest.mark.parametrize("text", ["pipeline = 1", "[pipeline]\nstore = 1"])
def test_local_settings_reject_wrong_types(tmp_path, monkeypatch, text):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    directory = tmp_path / ".provium"
    directory.mkdir()
    (directory / "config.toml").write_text(text)
    with pytest.raises((TypeError, ValueError), match="pipeline"):
        assert_pipeline_is_selected(parse("-f", "graph.yaml"))


def test_local_preparation_requires_one_graph():
    with pytest.raises(ValueError, match="exactly one"):
        load_pipeline()
