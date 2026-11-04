"""External value parsing uses codecs without weakening Python validation."""

import pytest

from provium import FilePath, ProcedureInputs, input_field, multi_input_field
from provium.cli.commands.execute import ExecuteCommand


class Inputs(ProcedureInputs):
    count = input_field(int)
    scale = input_field(float)
    source = input_field(FilePath)
    counts = multi_input_field(int, optional=True, minimum=2)


def test_cli_parses_values_and_collections():
    values = ExecuteCommand._read_bindings(
        Inputs.fields,
        ["count=3", "scale=1.5", "source=s3://bucket/data", "counts=2", "counts=3"],
    )
    assert values == {
        "count": 3,
        "scale": 1.5,
        "source": FilePath("s3://bucket/data"),
        "counts": [2, 3],
    }


@pytest.mark.parametrize("assignment", ["count=3.9", "scale=nan", "counts=oops"])
def test_cli_invalid_values_have_field_context(assignment):
    with pytest.raises((TypeError, ValueError), match=assignment.split("=")[0]):
        ExecuteCommand._read_bindings(Inputs.fields, [assignment])


def test_cli_rejects_duplicate_single_values():
    with pytest.raises(ValueError, match="only once"):
        ExecuteCommand._read_bindings(Inputs.fields, ["count=1", "count=2"])


def test_help_describes_value_types_and_optional_nonempty_minimum(capsys):
    from provium import ProcedureContract

    class Contract(ProcedureContract[None]):
        Inputs = Inputs

    ExecuteCommand._print_fields("Inputs", Contract.metadata.inputs, "Accepts")
    output = capsys.readouterr().out
    assert "Type: int" in output
    assert "0 or 2 or more" in output
