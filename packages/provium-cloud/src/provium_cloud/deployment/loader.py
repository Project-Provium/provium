"""Safe loading of a single local deployment document."""

from pathlib import Path
from typing import Any

import yaml

from provium_cloud.deployment.config import CloudDeployment


class _DeploymentLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: Any, node: yaml.MappingNode) -> dict[str, object]:
    result: dict[str, object] = {}
    for key_node, value_node in node.value:
        key: object = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise TypeError("deployment YAML keys must be strings")
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


_DeploymentLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def load_deployment(path: str | Path) -> CloudDeployment:
    """Read and validate configuration without connecting to its backends."""
    with Path(path).open(encoding="utf-8") as stream:
        value: object = yaml.load(stream, Loader=_DeploymentLoader)
    return CloudDeployment.model_validate(value)
