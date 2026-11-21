"""Shared AWS deployment and invocation arguments."""

import argparse
import os
from typing import Any

from provium.configuration import parse_override
from provium.procedure.config import compose_configuration

from provium_cloud.deployment import CloudDeployment, load_deployment
from provium_cloud.invocations.models import InvocationPlan
from provium_cloud.invocations.planning import prepare_pipeline, prepare_procedure


def configure_deployment(
    parser: argparse.ArgumentParser, *, worker: bool = False
) -> None:
    parser.add_argument(
        "--deployment", "-d", required=not worker, help="Deployment YAML file"
    )
    parser.add_argument(
        "--deployment-set",
        action="append",
        default=[],
        metavar="KEY.PATH=VALUE",
        help="Override any deployment setting (repeatable)",
    )
    parser.add_argument("--region")


def deployment(arguments: argparse.Namespace) -> CloudDeployment:
    config = (
        load_deployment(arguments.deployment)
        if arguments.deployment
        else CloudDeployment.model_validate_json(os.environ["PROVIUM_CLOUD_DEPLOYMENT"])
    )
    layers = [
        config.model_dump(mode="json"),
        *(parse_override(value) for value in arguments.deployment_set),
    ]
    if arguments.region:
        layers.append({"region": arguments.region})
    return CloudDeployment.model_validate(compose_configuration(layers))


def configure_invocation(parser: argparse.ArgumentParser) -> None:
    configure_deployment(parser)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--file", "-f", help="Unnamed pipeline graph YAML")
    selector.add_argument("--procedure", help="Procedure identifier")
    parser.add_argument("--input", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument(
        "--parameter",
        action="append",
        default=[],
        metavar="KEY.PATH=VALUE",
        help="Standalone procedure configuration",
    )
    parser.add_argument(
        "--tag", action="append", default=[], help="Standalone procedure scheduling tag"
    )
    parser.add_argument(
        "--target", action="append", help="Pipeline target alias or nodes.NAME.OUTPUT"
    )
    force = parser.add_mutually_exclusive_group()
    force.add_argument("--force", action="store_true")
    force.add_argument(
        "--force-selector",
        action="append",
        help="nodes.NAME, procedures.ID, or tags.TAG",
    )
    parser.add_argument("--job-queue")
    parser.add_argument("--job-definition")
    parser.add_argument(
        "--timeout", help="Job timeout: seconds, or duration such as 15m"
    )


def assignments(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for assignment in values:
        key, separator, value = assignment.partition("=")
        if not separator or not key or key in result:
            raise ValueError("inputs require unique NAME=VALUE assignments")
        result[key] = value
    return result


def invocation(
    arguments: argparse.Namespace, config: CloudDeployment
) -> InvocationPlan:
    overrides = {
        key: getattr(arguments, key)
        for key in ("job_queue", "job_definition", "timeout")
        if getattr(arguments, key) is not None
    }
    if arguments.procedure:
        if arguments.target or arguments.force_selector:
            raise ValueError("targets and force selectors require a pipeline")
        return prepare_procedure(
            arguments.procedure,
            config,
            configuration=compose_configuration(
                [parse_override(value) for value in arguments.parameter]
            ),
            tags=arguments.tag,
            force=arguments.force,
            overrides=overrides,
        )
    if arguments.parameter or arguments.tag:
        raise ValueError("parameters and tags require a standalone procedure")
    return prepare_pipeline(
        arguments.file,
        config,
        targets=arguments.target,
        force=arguments.force_selector or arguments.force,
        overrides=overrides,
    )


def json_result(value: Any) -> Any:
    from provium_cloud.state.models import Run, RunPage

    if isinstance(value, Run):
        return {**value.model_dump(mode="json"), "progress": value.progress}
    if isinstance(value, RunPage):
        return {
            "items": [json_result(run) for run in value.items],
            "next_cursor": value.next_cursor,
        }
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value
