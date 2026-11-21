"""Run from the package directory: python -m infrastructure.aws run."""

import argparse
import signal
import subprocess
import uuid
from pathlib import Path
from types import FrameType

from .config import ROOT, Settings, load_environment
from .lifecycle import Environment


def interrupted(signum: int, frame: FrameType | None) -> None:
    # Allow the first signal to enter finally; repeated signals must not skip cleanup.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt("Interrupted; cleaning up AWS test resources.")


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Disposable provium-cloud AWS tests")
    parser.add_argument("action", choices=("run", "cleanup"))
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Override root .env and .env.local; shell environment wins",
    )
    parser.add_argument(
        "--results", type=Path, help="Run journal and test reports directory"
    )
    args = parser.parse_args(arguments)
    if args.action == "cleanup" and args.results is None:
        parser.error("cleanup requires --results from the original run")
    load_environment(args.env_file)
    directory = args.results or ROOT / ".cloud-live" / uuid.uuid4().hex[:12]
    print(f"Results and recovery journal: {directory.resolve()}", flush=True)
    if args.action == "run":
        settings = Settings.from_environment()
        subprocess.run(
            ["docker", "info"], check=True, stdout=subprocess.DEVNULL, timeout=15
        )
        environment = Environment(settings, directory)
    else:
        environment = Environment.resume(directory)
    previous = {
        sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        if args.action == "run":
            environment.run()
        else:
            environment.cleanup()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print("AWS test resources deleted.", flush=True)


if __name__ == "__main__":
    main()
