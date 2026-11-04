import pytest

from provium.input_types import input_type, named_input_type


def test_codec_name_resolves_registered_type():
    assert named_input_type("int") is input_type(int)
    assert named_input_type("float") is input_type(float)
    with pytest.raises(ValueError, match="unregistered input type"):
        named_input_type("not-registered")
