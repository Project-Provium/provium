"""Cache maintenance commands."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING

from provium.cli.catalog import CommandCatalog
from provium.cli.command import Command
from provium.cli.group import CommandGroup

from provium_pipeline.cli.arguments import apply_configuration, configure_storage

if TYPE_CHECKING:
    from provium_pipeline.cache import MaintenanceGuard


class ClearCommand(Command):
    name = "clear"
    help = "Remove unused cached computations, optionally filtered by age or key"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        self.configure_storage(parser)
        parser.add_argument("--key", action="append")
        parser.add_argument("--older-than", type=float, default=0, metavar="SECONDS")
        parser.add_argument("--max-bytes", type=int)

    def configure_storage(self, parser: argparse.ArgumentParser) -> None:
        configure_storage(parser)

    def prepare(self, arguments: argparse.Namespace) -> MaintenanceGuard | None:
        apply_configuration(arguments)
        return None

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            guard = self.prepare(arguments)
            from provium_pipeline.cache import clear_cache
            from provium_pipeline.store import ArtifactStore

            with ArtifactStore(
                arguments.store, scratch=arguments.scratch_directory
            ) as store:
                result = clear_cache(
                    store,
                    keys=arguments.key,
                    older_than=arguments.older_than,
                    max_bytes=arguments.max_bytes,
                    guard=guard,
                )
            if arguments.json:
                print(json.dumps(asdict(result)))
            else:
                print(
                    f"Removed {len(result.removed)} cache entries "
                    f"({result.bytes_removed} bytes)."
                )
        except Exception as error:
            print(f"provium cache: {error}", file=sys.stderr)
            return 2
        return 0


class CacheCommand(CommandGroup):
    name = "cache"
    help = "Manage reusable pipeline artifacts"
    catalog = CommandCatalog()


CacheCommand.catalog.register(ClearCommand)
