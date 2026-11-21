"""Resolve the database secret inside the task, then execute the Batch command."""

import json
import os
import sys

import boto3
from botocore.config import Config
from psycopg.conninfo import make_conninfo


def main(arguments: list[str]) -> None:
    if not arguments:
        raise ValueError("A container command is required.")
    client = boto3.client(
        "secretsmanager",
        config=Config(retries={"mode": "standard", "total_max_attempts": 3}),
    )
    secret = json.loads(
        client.get_secret_value(SecretId=os.environ["DB_SECRET_ARN"])["SecretString"]
    )
    connection = make_conninfo(
        host=os.environ["DB_HOST"],
        port=5432,
        dbname="proviumcloud",
        user=secret["username"],
        password=secret["password"],
        sslmode="require",
        connect_timeout=10,
    )
    os.execvpe(
        arguments[0],
        arguments,
        {**os.environ, "PROVIUM_CLOUD_DATABASE_URL": connection},
    )


if __name__ == "__main__":
    main(sys.argv[1:])
