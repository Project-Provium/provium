"""Foreground serial dispatch execution."""

from collections.abc import Callable


class SerialRunner[LeaseT]:
    """Claim and execute one task at a time until a dispatch is terminal."""

    def __init__(
        self,
        *,
        claim: Callable[[], LeaseT | None],
        execute: Callable[[LeaseT], None],
        is_terminal: Callable[[], bool],
        wait_for_work: Callable[[], None],
    ) -> None:
        self._claim = claim
        self._execute = execute
        self._is_terminal = is_terminal
        self._wait_for_work = wait_for_work

    def run(self) -> None:
        """Run until terminal, yielding through the injected idle wait hook."""
        while not self._is_terminal():
            lease = self._claim()
            if lease is None:
                self._wait_for_work()
                continue
            self._execute(lease)


__all__ = ["SerialRunner"]
