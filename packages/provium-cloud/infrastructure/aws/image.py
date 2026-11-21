"""Build only workspace package sources; ECR credentials stay outside the context."""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .config import ROOT

HERE = Path(__file__).parent
PACKAGES = (
    "packages/provium",
    "packages/provium-pipeline",
    "packages/provium-cloud",
    "examples/provium-text-pipeline",
)


def prepare_context(repository: Path, destination: Path) -> None:
    for package in PACKAGES:
        source, target = repository / package, destination / package
        target.mkdir(parents=True)
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            if (source / name).is_file():
                shutil.copyfile(source / name, target / name)
        shutil.copytree(
            source / "src",
            target / "src",
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.egg-info", ".env*"
            ),
        )
    shutil.copyfile(HERE / "Dockerfile", destination / "Dockerfile")
    runtime = destination / "runtime"
    runtime.mkdir()
    for source in (HERE / "runtime").glob("*.py"):
        shutil.copyfile(source, runtime / source.name)


def build_image(ecr: Any, repository: str, directory: Path) -> None:
    with TemporaryDirectory(prefix="provium-cloud-image-") as temp:
        root = Path(temp)
        context, config = root / "context", root / "docker"
        context.mkdir()
        config.mkdir(mode=0o700)
        original = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")))
        if (original / "config.json").is_file():
            current = json.loads((original / "config.json").read_text())
            (config / "config.json").write_text(
                json.dumps({"currentContext": current.get("currentContext", "default")})
            )
        if (original / "contexts").is_dir():
            shutil.copytree(original / "contexts", config / "contexts")
        prepare_context(ROOT, context)
        token = ecr.get_authorization_token()["authorizationData"][0]
        user, password = (
            base64.b64decode(token["authorizationToken"]).decode().split(":", 1)
        )
        env = {**os.environ, "DOCKER_CONFIG": str(config)}
        with (directory / "image.log").open("w") as log:
            subprocess.run(
                [
                    "docker",
                    "login",
                    "--username",
                    user,
                    "--password-stdin",
                    repository.split("/")[0],
                ],
                input=password,
                text=True,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=60,
            )
            subprocess.run(
                [
                    "docker",
                    "buildx",
                    "build",
                    "--platform",
                    "linux/amd64",
                    "--load",
                    "-t",
                    f"{repository}:test",
                    str(context),
                ],
                env=os.environ,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=1200,
            )
            subprocess.run(
                ["docker", "push", f"{repository}:test"],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=600,
            )
