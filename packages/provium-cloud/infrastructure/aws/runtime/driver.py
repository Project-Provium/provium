"""Run the live suite and publish machine-readable results even on test failure."""

import json
import os
from pathlib import Path

import boto3
import pytest
from botocore.config import Config

REPORTS = Path("/tmp/cloud-reports")


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    result = int(
        pytest.main(
            [
                str(Path(__file__).with_name("live_test.py")),
                "-v",
                "--tb=short",
                f"--junitxml={REPORTS / 'junit.xml'}",
            ]
        )
    )
    (REPORTS / "summary.json").write_text(json.dumps({"exit_code": result}))
    s3 = boto3.client(
        "s3", config=Config(retries={"mode": "standard", "total_max_attempts": 3})
    )
    for report in REPORTS.iterdir():
        s3.upload_file(str(report), os.environ["TEST_BUCKET"], f"reports/{report.name}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
