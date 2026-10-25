"""Queue names shared by persistence and its consumers."""


def assert_queue_name_is_valid(name: str) -> str:
    """Require a nonempty name without surrounding whitespace."""
    if not isinstance(name, str):
        raise TypeError("queue name must be a string")
    if not name or name != name.strip():
        raise ValueError("queue name must be nonempty without surrounding whitespace")
    return name
