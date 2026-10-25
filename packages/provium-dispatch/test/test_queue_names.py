import pytest

from provium_dispatch.persistence.queues import assert_queue_name_is_valid


def test_assert_queue_name_preserves_valid_name():
    assert assert_queue_name_is_valid("video-processing") == "video-processing"


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("", ValueError),
        (" queue", ValueError),
        ("queue ", ValueError),
        (None, TypeError),
        (1, TypeError),
    ],
)
def test_assert_queue_name_rejects_invalid_names(name, error):
    with pytest.raises(error):
        assert_queue_name_is_valid(name)
