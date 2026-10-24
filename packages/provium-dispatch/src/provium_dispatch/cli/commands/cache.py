"""Queue-aware cache maintenance."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from provium.cli.catalog import CommandCatalog
from provium_pipeline.cli.commands.cache import CacheCommand as BasicCacheCommand
from provium_pipeline.cli.commands.cache import ClearCommand as BasicClearCommand

from ..arguments import configure_storage, require_database

if TYPE_CHECKING:
    from provium_pipeline.cache import MaintenanceGuard


class ClearCommand(BasicClearCommand):
    def configure_storage(self, parser: argparse.ArgumentParser) -> None:
        configure_storage(parser)

    def prepare(self, arguments: argparse.Namespace) -> MaintenanceGuard:
        require_database(arguments)
        from ...persistence.factory import open_backend

        return open_backend(arguments.database)


class CacheCommand(BasicCacheCommand):
    catalog = CommandCatalog()


CacheCommand.catalog.register(ClearCommand)
