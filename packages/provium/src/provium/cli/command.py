"""Base type for Provium command-line commands."""

from __future__ import annotations

from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace
from typing import ClassVar


class Command(ABC):
    """A command that can configure arguments and execute them."""

    name: ClassVar[str]
    help: ClassVar[str]
    add_help: ClassVar[bool] = True

    @abstractmethod
    def configure(self, parser: ArgumentParser) -> None:
        """Configure the command's arguments."""

    def configuration(self, arguments: Namespace) -> dict[str, object]:
        """Load application settings only when this command needs them."""
        from provium.cli.configuration import command_configuration

        return command_configuration(arguments)

    @abstractmethod
    def execute(self, arguments: Namespace) -> int:
        """Execute the command and return its process exit code."""


__all__ = ["Command"]
