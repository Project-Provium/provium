from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

PROJECT = Path(__file__).parents[1]
REPOSITORY = PROJECT.parents[1]


def run_cli(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    python_paths = (
        REPOSITORY / "packages" / "provium" / "src",
        PROJECT / "src",
    )
    environment["PYTHONPATH"] = os.pathsep.join(map(str, python_paths))
    return subprocess.run(
        [sys.executable, "-m", "provium", *arguments],
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def run_python(script: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (
            str(REPOSITORY / "packages" / "provium" / "src"),
            str(PROJECT / "src"),
        )
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_installed_catalogs_complete_the_cli_pipeline(tmp_path: Path) -> None:
    document_text = "Provium tracks artifacts\nfrom beginning to end."
    source = tmp_path / "document.txt"
    document = tmp_path / "document.pa"
    document_dump = tmp_path / "document-copy.txt"
    tokens = tmp_path / "tokens.pa"
    dumped = tmp_path / "tokens.txt"
    source.write_text(document_text, encoding="utf-8")

    loaded = run_cli(
        "artifact",
        "load",
        "provium_text_pipeline_example.DocumentV1",
        str(source),
        str(document),
        cwd=tmp_path,
    )
    executed = run_cli(
        "execute",
        "provium_text_pipeline_example.TokenizeV1",
        "--input",
        f"source={document}",
        "--output",
        f"destination={tokens}",
        cwd=tmp_path,
    )
    dumped_document = run_cli(
        "artifact",
        "dump",
        str(document),
        str(document_dump),
        cwd=tmp_path,
    )
    dumped_result = run_cli(
        "artifact",
        "dump",
        str(tokens),
        str(dumped),
        cwd=tmp_path,
    )

    assert loaded.stdout == loaded.stderr == ""
    assert dumped_document.stdout == dumped_document.stderr == ""
    assert document_dump.read_text(encoding="utf-8") == document_text
    assert str(UUID(executed.stdout.strip())) == executed.stdout.strip()
    assert executed.stderr == ""
    assert dumped_result.stdout == dumped_result.stderr == ""
    assert dumped.read_text(encoding="utf-8").splitlines() == document_text.split()

    lineage = run_python(
        f"""
import json
from provium import read_artifact_header

header = read_artifact_header({str(tokens)!r})
record = header.lineage.artifacts[header.artifact_identity]
execution = header.lineage.executions[record.producer_execution_identity]
print(json.dumps({{
    "procedure": execution.procedure.name,
    "inputs": [value.artifact_identifier for value in execution.inputs],
    "output": record.reference.artifact_identifier,
}}))
""",
        cwd=tmp_path,
    )
    assert json.loads(lineage.stdout) == {
        "procedure": "provium_text_pipeline_example.TokenizeV1",
        "inputs": ["provium_text_pipeline_example.DocumentV1"],
        "output": "provium_text_pipeline_example.TokensV1",
    }


def test_catalogs_are_discoverable_without_importing_implementations(
    tmp_path: Path,
) -> None:
    script = """
import json
import sys
from provium import discover_artifact_catalogs, discover_procedure_catalogs

artifacts = sorted(discover_artifact_catalogs().definitions)
procedures = sorted(discover_procedure_catalogs().definitions)
implementation_modules = sorted(
    name for name in sys.modules
    if name.endswith((".document", ".tokens", ".implementation"))
)
print(json.dumps([artifacts, procedures, implementation_modules]))
"""
    result = run_python(script, cwd=tmp_path)

    assert result.stdout == (
        '[["provium_text_pipeline_example.DocumentV1", '
        '"provium_text_pipeline_example.TokensV1", '
        '"provium_text_pipeline_example.WordCountV1"], '
        '["provium_text_pipeline_example.CountWordsV1", '
        '"provium_text_pipeline_example.TokenizeV1"], []]\n'
    )
    assert result.stderr == ""
