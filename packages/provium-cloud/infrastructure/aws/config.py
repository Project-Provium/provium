"""Local and CI configuration for disposable AWS tests."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[4]


def load_environment(path: Path | None = None) -> None:
    """Load root .env, .env.local, then an explicit file; the shell wins."""
    if path is not None and not path.is_file():
        raise FileNotFoundError(path)
    files = [ROOT / ".env", ROOT / ".env.local"]
    if path is not None:
        files.append(path)
    values: dict[str, str] = {}
    for file in files:
        if file.exists():
            with file.open(encoding="utf-8") as stream:
                values.update(
                    {
                        key: value
                        for key, value in dotenv_values(
                            stream=stream, interpolate=False
                        ).items()
                        if value is not None
                    }
                )
    for key, value in values.items():
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    region: str
    account_id: str
    role_arn: str
    stack_prefix: str = "provium-cloud-test"
    test_timeout: int = 1800

    @classmethod
    def from_environment(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION", "")
        account = env.get("PROVIUM_CLOUD_TEST_ACCOUNT_ID", "")
        role = env.get("CLOUDFORMATION_ROLE_ARN", "")
        prefix = env.get("PROVIUM_CLOUD_TEST_STACK_PREFIX", "provium-cloud-test")
        timeout = int(env.get("PROVIUM_CLOUD_TEST_TIMEOUT", "1800"))
        if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region):
            raise ValueError("Set AWS_REGION or AWS_DEFAULT_REGION (commercial AWS).")
        if not re.fullmatch(r"\d{12}", account):
            raise ValueError(
                "Set PROVIUM_CLOUD_TEST_ACCOUNT_ID to the test account ID."
            )
        if not re.fullmatch(rf"arn:aws:iam::{account}:role/[\w+=,.@/-]+", role):
            raise ValueError("CLOUDFORMATION_ROLE_ARN must belong to the test account.")
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,47}", prefix):
            raise ValueError("Stack prefix must be 1–48 letters, numbers, or hyphens.")
        if not 60 <= timeout <= 3600:
            raise ValueError("Test timeout must be between 60 and 3600 seconds.")
        return cls(region, account, role, prefix, timeout)
