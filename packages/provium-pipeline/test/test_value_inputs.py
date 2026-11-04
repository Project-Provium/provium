"""Single-value declarations survive graph and definition serialization."""

from dataclasses import dataclass

import pytest
from provium import (
    FilePath,
    Procedure,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureInputs,
    ProcedureOutputs,
    input_field,
    output,
    register_input_type,
)
from provium_text_pipeline_example.artifact import DOCUMENT

from provium_pipeline import ExecutionContext, InputSet
from provium_pipeline.models import PipelineConfiguration, PipelineDefinition
from provium_pipeline.planning import build_plan
from provium_pipeline.store import ArtifactStore
from provium_pipeline.values import encode_value, value_declaration


@dataclass(frozen=True)
class Labels:
    names: tuple[str, ...]


LABELS = register_input_type(
    Labels,
    name="pipeline-test-labels-v1",
    decode=lambda value: Labels(tuple(value)),
    encode=lambda value: list(value.names),
)


class ImportContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        scale = input_field(float)

    class Inputs(ProcedureInputs):
        source = input_field(FilePath)
        count = input_field(int)
        maybe = input_field(int, optional=True)
        labels = input_field(Labels, optional=True)

    class Outputs(ProcedureOutputs):
        result = output(DOCUMENT)


class ImportProcedure(
    Procedure[
        None, ImportContract.SetupInputs, ImportContract.Inputs, ImportContract.Outputs
    ]
):
    def setup(self, context, configuration, inputs):
        self.scale = inputs.scale

    def process(self, context, configuration, inputs, outputs):
        with outputs.result.open() as writer:
            writer.write(f"{inputs.source}:{inputs.count * self.scale}:{inputs.maybe}")


IMPORT = ProcedureDefinition(
    "pipeline.tests.Import",
    f"{__name__}:ImportProcedure",
    "Import",
    None,
    ImportContract,
)
ImportProcedure.definition = IMPORT


def import_graph():
    return PipelineConfiguration(
        name="import",
        inputs={
            "source": {"value_type": "file-path"},
            "count": {"value_type": "int"},
            "scale": {"value_type": "float"},
        },
        nodes={
            "load": {
                "procedure": IMPORT.identifier,
                "inputs": {"source": "$inputs.source", "count": "$inputs.count"},
                "setup_inputs": {"scale": "$inputs.scale"},
            }
        },
        outputs={"result": "$nodes.load.result"},
    )


def test_value_execution_and_cache(tmp_path):
    store = ArtifactStore(str(tmp_path / "store"))
    item = InputSet(
        {"source": FilePath("s3://bucket/source.csv"), "count": 3, "scale": 2.5}
    )
    for starts in (1, 0):
        with ExecutionContext(
            import_graph(), store, procedures={IMPORT.identifier: IMPORT}
        ) as run:
            run.start([item])
        assert run.outcomes[0].state == "completed", run.outcomes[0].error
        assert run.worker_starts == starts
        from provium_pipeline.input_sets import input_set_hash, inspect_inputs
        from provium_pipeline.lookup import lookup_artifact

        pinned = inspect_inputs(store, item.inputs, run.plan.input_types)
        found = lookup_artifact(run.plan, store, pinned, "import.outputs.result")
        assert found.fingerprint == run.outcomes[0].outputs["result"].fingerprint
        changed = inspect_inputs(
            store, {**item.inputs, "count": 4}, run.plan.input_types
        )
        assert input_set_hash(pinned) != input_set_hash(changed)
        assert input_set_hash(inspect_inputs(store, item.inputs)) == input_set_hash(
            pinned
        )


@pytest.mark.parametrize("labels", [Labels(("a", "b")), "https://host/labels"])
def test_custom_value_round_trip_and_optional_bound_value(tmp_path, labels):
    from provium_pipeline.input_sets import inspect_inputs
    from provium_pipeline.lookup import lookup_artifact
    from provium_pipeline.requests import ExecutionRequest

    graph = import_graph()
    graph.inputs["labels"] = value_declaration(Labels)
    graph.inputs["maybe"] = value_declaration(int)
    graph.nodes["load"].inputs.update(labels="$inputs.labels", maybe="$inputs.maybe")
    values = {
        "source": "source.csv",
        "count": "3",
        "scale": "2.5",
        "labels": labels,
        "maybe": None,
    }
    store = ArtifactStore(str(tmp_path / "store"))
    pinned = inspect_inputs(store, values, graph.inputs)
    for inputs in (values, pinned):
        with ExecutionContext(
            graph, store, procedures={IMPORT.identifier: IMPORT}
        ) as run:
            run.start(
                [ExecutionRequest(inputs, {"result": str(tmp_path / "result.pa")})]
            )
        assert run.outcomes[0].state == "completed", run.outcomes[0].error
        assert lookup_artifact(run.plan, store, pinned, "import.outputs.result")
    pinned["count"]["value_input"]["value"] = None
    with pytest.raises(ValueError, match="required value"):
        lookup_artifact(run.plan, store, pinned, "import.outputs.result")


def test_value_plan_preserves_types_and_rejects_mismatch():
    graph = import_graph()
    plan = build_plan(graph, procedures={IMPORT.identifier: IMPORT})
    assert plan.input_types == graph.inputs
    definition = PipelineDefinition(
        inputs={"source": FilePath, "count": int, "scale": float},
        outputs={"result": DOCUMENT},
    )
    assert (
        build_plan(graph, definition, {IMPORT.identifier: IMPORT}).input_types
        == graph.inputs
    )
    graph.inputs["count"] = {"value_type": "float"}
    with pytest.raises(ValueError, match="incompatible"):
        build_plan(graph, procedures={IMPORT.identifier: IMPORT})


@pytest.mark.parametrize(
    "kind,name,value",
    [
        (int, "int", 3),
        (float, "float", 3.9),
        (FilePath, "file-path", FilePath("s3://bucket/source.csv")),
    ],
)
def test_value_declarations_and_snapshots(kind, name, value):
    declaration = {"value_type": name}
    assert value_declaration(kind) == declaration
    definition = PipelineDefinition(inputs={"source": kind}, outputs={})
    assert definition.snapshot()["inputs"] == {"source": declaration}
    graph = PipelineConfiguration(
        name="import", inputs={"source": declaration}, nodes={}, outputs={}
    )
    assert PipelineConfiguration.model_validate_json(graph.model_dump_json()) == graph
    payload = encode_value(declaration, value)
    assert payload["value_input"]["type"] == name
    assert encode_value(declaration, payload) == payload


@pytest.mark.parametrize("value", [None, 3.9, True, "3.9"])
def test_invalid_runtime_value_is_an_item_failure(tmp_path, value):
    with ExecutionContext(
        import_graph(),
        ArtifactStore(str(tmp_path / "cache")),
        procedures={IMPORT.identifier: IMPORT},
    ) as run:
        run.start([InputSet({"source": "source.csv", "count": value, "scale": "2.5"})])
    assert run.outcomes[0].state == "failed"
    assert run.worker_starts == 0


def test_value_only_output_is_rejected():
    graph = PipelineConfiguration(
        name="invalid",
        inputs={"n": {"value_type": "int"}},
        nodes={},
        outputs={"n": "$inputs.n"},
    )
    with pytest.raises(ValueError, match="outputs must be artifacts"):
        build_plan(graph)


@pytest.mark.parametrize("declaration", [{}, {"value_type": ""}, {"other": "int"}])
def test_invalid_value_declaration(declaration):
    with pytest.raises(ValueError):
        PipelineConfiguration(
            name="invalid", inputs={"n": declaration}, nodes={}, outputs={}
        )


def test_codec_errors_and_optional_snapshot():
    from provium.input_types import input_type, named_input_type

    assert value_declaration(input_type(int)) == {"value_type": "int"}
    assert value_declaration(DOCUMENT) == DOCUMENT.identifier
    assert value_declaration("artifact") == "artifact"
    with pytest.raises(ValueError, match="unregistered"):
        named_input_type("missing-codec")
    with pytest.raises(ValueError, match="incompatible"):
        encode_value(
            {"value_type": "int"}, {"value_input": {"type": "float", "value": 3}}
        )
    assert (
        encode_value(
            {"value_type": "int"}, {"value_input": {"type": "int", "value": None}}
        )["value_input"]["value"]
        is None
    )


def test_cli_and_csv_value_inputs(tmp_path, monkeypatch, capsys):
    import json
    from types import SimpleNamespace

    import yaml
    from provium.cli import run

    import provium_pipeline.planning as planning

    monkeypatch.setattr(
        planning,
        "discover_procedure_catalogs",
        lambda: SimpleNamespace(resolve=lambda name: IMPORT),
    )
    graph = import_graph()
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(graph.model_dump(mode="json")))
    flags = ["-f", str(path), "--store", str(tmp_path / "store")]
    values = [
        "--input",
        "source=s3://bucket/data.csv",
        "--input",
        "count=3",
        "--input",
        "scale=2.5",
    ]
    assert (
        run(
            [
                "pipeline",
                "run",
                *flags,
                *values,
                "--output",
                f"result={tmp_path / 'out.pa'}",
            ]
        )
        == 0
    )
    assert (
        run(["pipeline", "lookup", "import.outputs.result", *flags, *values, "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["artifact"]
    csv = tmp_path / "requests.csv"
    csv.write_text(
        "inputs.source,inputs.count,inputs.scale,result\n"
        f"s3://bucket/data.csv,4,2.5,{tmp_path / 'csv.pa'}\n"
    )
    assert run(["pipeline", "run", *flags, "--csv", str(csv)]) == 0
    assert (tmp_path / "csv.pa").exists()


def test_publication_value_snapshots_and_path_collisions(tmp_path):
    from provium_pipeline.publication import FilePublisher
    from provium_pipeline.requests import ExecutionRequest

    store = ArtifactStore(str(tmp_path / "cache"))
    publisher = FilePublisher(store)
    publisher.assert_requests_are_valid(
        [
            ExecutionRequest(
                {
                    "number": encode_value({"value_type": "int"}, 3),
                    "absent": encode_value({"value_type": "file-path"}, None),
                    "path": encode_value(
                        {"value_type": "file-path"}, str(tmp_path / "source")
                    ),
                },
                {"result": str(tmp_path / "out")},
            )
        ]
    )
    publisher = FilePublisher(store, input_types={"path": {"value_type": "file-path"}})
    with pytest.raises(ValueError, match="overwrites an input"):
        publisher.assert_requests_are_valid(
            [
                ExecutionRequest(
                    {"path": tmp_path / "source"}, {"result": str(tmp_path / "source")}
                )
            ]
        )


def test_worker_decodes_values_and_optional_absence():
    from provium_pipeline.workers import bindings

    assert bindings(
        IMPORT,
        "Inputs",
        {
            "source": "s3://bucket/data.csv",
            "count": 3,
            "maybe": None,
            "labels": ["a", "b"],
        },
    ) == {
        "source": FilePath("s3://bucket/data.csv"),
        "count": 3,
        "maybe": None,
        "labels": Labels(("a", "b")),
    }


def test_integer_rejects_fraction():
    with pytest.raises(TypeError, match="integer"):
        encode_value({"value_type": "int"}, 3.9)
