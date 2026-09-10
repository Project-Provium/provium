from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID


def run(
    *arguments: str,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        env={
            **os.environ,
            "PYTHONNOUSERSITE": "1",
            **({} if environment is None else environment),
        },
        check=check,
        capture_output=True,
        text=True,
    )


def test_installed_wheels_complete_external_plugin_workflow(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).parents[4]
    distributions = tmp_path / "dist"
    distributions.mkdir()
    projects = (
        repository / "packages" / "provium",
        Path(__file__).parent / "example_plugin",
        repository / "examples" / "provium-text-pipeline",
    )
    for project in projects:
        run(
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(distributions),
            str(project),
        )

    environment = tmp_path / "environment"
    run(sys.executable, "-m", "venv", "--system-site-packages", str(environment))
    python = environment / "bin" / "python"
    wheels = tuple(str(path) for path in sorted(distributions.glob("*.whl")))
    run(
        str(python),
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-deps",
        *wheels,
    )

    imported_paths = run(
        str(python),
        "-c",
        (
            "import sys; "
            "import provium, provium.cli, provium_example_plugin, "
            "provium_text_pipeline_example; "
            "assert 'provium_text_pipeline_example.artifact.document' "
            "not in sys.modules; "
            "assert 'provium_text_pipeline_example.artifact.tokens' "
            "not in sys.modules; "
            "assert 'provium_text_pipeline_example.procedure.tokenize.contract' "
            "not in sys.modules; "
            "assert 'provium_text_pipeline_example.procedure.tokenize.implementation' "
            "not in sys.modules; "
            "print(provium.__file__); print(provium.cli.__file__); "
            "print(provium_example_plugin.__file__); "
            "print(provium_text_pipeline_example.__file__)"
        ),
        cwd=tmp_path,
    ).stdout.splitlines()
    assert len(imported_paths) == 4
    assert all(Path(path).is_relative_to(environment) for path in imported_paths)

    public_contracts = (
        "ArtifactDefinition",
        "Artifact",
        "ArtifactReadBinding",
        "ArtifactWriteBinding",
        "ProcedureDefinition",
        "ProcedureContract",
        "ProcedureInputs",
        "ProcedureOutputs",
        "Procedure",
        "ProcedureExecutor",
        "PreparedProcedure",
        "ProcedureConfig",
        "ConfigurationSnapshot",
    )
    contract_audit = run(
        str(python),
        "-c",
        (
            "import inspect, json, provium; "
            f"names = {public_contracts!r}; "
            "audit = {name: {'module': getattr(provium, name).__module__, "
            "'signature': str(inspect.signature(getattr(provium, name)))} "
            "for name in names}; "
            "print(json.dumps(audit, sort_keys=True))"
        ),
        cwd=tmp_path,
    )
    audited_contracts = json.loads(contract_audit.stdout)
    assert tuple(sorted(audited_contracts)) == tuple(sorted(public_contracts))
    assert all(
        contract["module"].startswith("provium") and contract["signature"]
        for contract in audited_contracts.values()
    )

    installed_version = run(
        str(python),
        "-c",
        "from importlib.metadata import version; print(version('provium'))",
        cwd=tmp_path,
    ).stdout.strip()
    version = run(str(python), "-m", "provium", "--version", cwd=tmp_path)
    assert version.stdout == f"provium {installed_version}\n"
    listed = run(str(python), "-m", "provium", "execute", "-l", cwd=tmp_path)
    assert "smoke.SourceTextV1\tSource text" in listed.stdout
    assert "smoke.TransformTextV1\tTransform text" in listed.stdout
    assert "smoke.FailingTextV1\tFailing text" in listed.stdout
    assert "provium_text_pipeline_example.TokenizeV1\tTokenize" in listed.stdout

    lazy_example = run(
        str(python),
        "-c",
        (
            "import sys; "
            "from provium import discover_artifact_catalogs, "
            "discover_procedure_catalogs; "
            "discover_artifact_catalogs(); discover_procedure_catalogs(); "
            "forbidden = {'provium_text_pipeline_example.artifact.document', "
            "'provium_text_pipeline_example.artifact.tokens', "
            "'provium_text_pipeline_example.procedure.tokenize.contract', "
            "'provium_text_pipeline_example.procedure.tokenize.implementation'}; "
            "assert forbidden.isdisjoint(sys.modules), "
            "sorted(forbidden.intersection(sys.modules))"
        ),
        cwd=tmp_path,
    )
    assert lazy_example.stdout == ""

    sentinel = tmp_path / "imports.log"
    sentinel_environment = {"PROVIUM_TEST_IMPORT_SENTINEL": str(sentinel)}
    shown = run(
        str(python),
        "-m",
        "provium",
        "execute",
        "smoke.TransformTextV1",
        "--help",
        cwd=tmp_path,
        environment=sentinel_environment,
    )
    assert "provium execute smoke.TransformTextV1" in shown.stdout
    assert not sentinel.exists()

    def create_source(name: str, text: str) -> Path:
        configuration = tmp_path / f"{name}.json"
        destination = tmp_path / f"{name}.provium"
        configuration.write_text(json.dumps({"text": text}), encoding="utf-8")
        executed = run(
            str(python),
            "-m",
            "provium",
            "execute",
            "smoke.SourceTextV1",
            "--config",
            str(configuration),
            "--output",
            f"value={destination}",
            cwd=tmp_path,
        )
        assert str(UUID(executed.stdout.strip())) == executed.stdout.strip()
        return destination

    setup = create_source("setup", "S")
    required = create_source("required", "R")
    optional = create_source("optional", "O")
    repeated_first = create_source("repeated-first", "A")
    repeated_second = create_source("repeated-second", "B")
    json_configuration = tmp_path / "transform.json"
    yaml_configuration = tmp_path / "transform.yaml"
    json_configuration.write_text(
        json.dumps({"prefix": "<", "suffix": "overridden"}),
        encoding="utf-8",
    )
    yaml_configuration.write_text('suffix: ">"\n', encoding="utf-8")
    transformed = tmp_path / "transformed.provium"
    summary = tmp_path / "summary.provium"
    transformed_execution = run(
        str(python),
        "-m",
        "provium",
        "execute",
        "smoke.TransformTextV1",
        "--config",
        str(json_configuration),
        "--config",
        str(yaml_configuration),
        "--setup-input",
        f"setup={setup}",
        "--input",
        f"required={required}",
        "--input",
        f"optional={optional}",
        "--input",
        f"repeated={repeated_first}",
        "--input",
        f"repeated={repeated_second}",
        "--output",
        f"transformed={transformed}",
        "--output",
        f"summary={summary}",
        cwd=tmp_path,
    ).stdout.strip()
    assert str(UUID(transformed_execution)) == transformed_execution

    input_paths = [
        str(path)
        for path in (
            setup,
            required,
            optional,
            repeated_first,
            repeated_second,
        )
    ]
    verification_script = f"""
import hashlib
from provium import ArtifactReference, session
from provium_example_plugin.artifacts import TextArtifact

input_paths = {input_paths!r}
output_paths = {[str(transformed), str(summary)]!r}
expected_bodies = ["<SROAB>", "inputs=5;characters=7"]
input_references = []
for path in input_paths:
    with session():
        with TextArtifact.bind_read(path).open() as reader:
            input_references.append(
                ArtifactReference(reader.identity, reader.artifact_identifier)
            )
output_headers = []
for path, expected in zip(output_paths, expected_bodies, strict=True):
    with session():
        with TextArtifact.bind_read(path).open() as reader:
            body = reader.read_text()
            header = reader.metadata
    assert body == expected
    assert header.body_length == len(expected.encode())
    assert header.body_digest == hashlib.sha256(expected.encode()).hexdigest()
    output_headers.append(header)
assert output_headers[0].artifact_identity != output_headers[1].artifact_identity
assert output_headers[0].lineage == output_headers[1].lineage
execution = output_headers[0].lineage.executions[{transformed_execution!r}]
assert execution.inputs == tuple(input_references)
assert {{reference.identity for reference in execution.outputs}} == {{
    header.artifact_identity for header in output_headers
}}
"""
    run(str(python), "-c", verification_script, cwd=tmp_path)

    console = run(
        str(environment / "bin" / "provium"),
        "execute",
        "-l",
        cwd=tmp_path,
    )
    assert console.stdout == listed.stdout

    existing = tmp_path / "existing.provium"
    existing.write_bytes(b"original")
    paths_before_failure = set(tmp_path.iterdir())
    failed = run(
        str(python),
        "-m",
        "provium",
        "execute",
        "smoke.FailingTextV1",
        "--input",
        f"source={required}",
        "--output",
        f"result={existing}",
        cwd=tmp_path,
        check=False,
    )
    assert failed.returncode == 2
    assert failed.stdout == ""
    assert "deliberate installed-plugin failure" in failed.stderr
    assert existing.read_bytes() == b"original"
    assert set(tmp_path.iterdir()) == paths_before_failure

    source_text = tmp_path / "example-input.txt"
    source_text.write_text("Hello hello world and Provium", encoding="utf-8")
    raw_text = tmp_path / "example-input.provium"
    loaded = run(
        str(python),
        "-m",
        "provium",
        "artifact",
        "load",
        "provium_text_pipeline_example.DocumentV1",
        str(source_text),
        str(raw_text),
        cwd=tmp_path,
    )
    assert loaded.stdout == loaded.stderr == ""
    tokens = tmp_path / "example-tokens.provium"
    tokenize_execution = run(
        str(python),
        "-m",
        "provium",
        "execute",
        "provium_text_pipeline_example.TokenizeV1",
        "--input",
        f"source={raw_text}",
        "--output",
        f"destination={tokens}",
        cwd=tmp_path,
    ).stdout.strip()
    assert str(UUID(tokenize_execution)) == tokenize_execution

    dumped_tokens = tmp_path / "example-tokens.txt"
    dumped = run(
        str(python),
        "-m",
        "provium",
        "artifact",
        "dump",
        str(tokens),
        str(dumped_tokens),
        cwd=tmp_path,
    )
    assert dumped.stdout == dumped.stderr == ""
    assert dumped_tokens.read_text(encoding="utf-8").splitlines() == [
        "Hello",
        "hello",
        "world",
        "and",
        "Provium",
    ]

    inspect_example_output = f"""
from provium import session
from provium_text_pipeline_example.artifact.tokens import TokensV1Artifact

with session():
    with TokensV1Artifact.bind_read({str(tokens)!r}).open() as reader:
        assert reader.read() == ["Hello", "hello", "world", "and", "Provium"]
        metadata = reader.metadata
assert metadata.artifact_identifier == "provium_text_pipeline_example.TokensV1"
execution = metadata.lineage.executions[{tokenize_execution!r}]
assert execution.procedure.name == "provium_text_pipeline_example.TokenizeV1"
assert len(execution.inputs) == 1
assert execution.inputs[0].artifact_identifier == (
    "provium_text_pipeline_example.DocumentV1"
)
"""
    run(str(python), "-c", inspect_example_output, cwd=tmp_path)
