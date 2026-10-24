"""Optional backends must not be required for local pipeline execution."""

import subprocess
import sys
import tomllib
from pathlib import Path


def test_backend_dependencies_are_optional():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())[
        "project"
    ]
    assert not any(
        requirement.startswith(("psycopg", "boto3"))
        for requirement in project["dependencies"]
    )
    extras = project["optional-dependencies"]
    assert "postgres" not in extras
    assert "boto3>=1.35" in extras["s3"]


def test_local_execution_without_optional_drivers(tmp_path):
    from test_store import make_artifact

    source = make_artifact(tmp_path / "source.pa")
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "kind: PipelineConfiguration\nname: local\n"
        "inputs: {document: provium_text_pipeline_example.DocumentV1}\n"
        "nodes: {}\ntargets: {copy: '$inputs.document'}\n"
    )
    code = f"""
import sys
sys.path = {sys.path!r}
import importlib.abc

class BlockDrivers(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {{"psycopg", "boto3", "botocore"}}:
            raise AssertionError("unexpected optional import: " + fullname)

sys.meta_path.insert(0, BlockDrivers())
from provium.cli import run
assert run(["pipeline", "run", "-f", {str(path)!r},
            "--input", {"document=" + str(source)!r},
            "--output", {"copy=" + str(tmp_path / "copy.pa")!r},
            "--store", {str(tmp_path / "store")!r}]) == 0
"""
    subprocess.run([sys.executable, "-c", code], check=True)
