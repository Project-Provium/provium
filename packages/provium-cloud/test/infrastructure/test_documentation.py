"""Validate runnable guide examples without contacting AWS."""

import json
import re
from pathlib import Path
from string import Template

import yaml

from provium_cloud.deployment import CloudDeployment
from provium_cloud.invocations.planning import prepare_pipeline, prepare_procedure

ROOT = Path(__file__).resolve().parents[4]


def test_cloud_guide_examples_plan_without_services():
    guide = (ROOT / "docs/guides/cloud.md").read_text()
    examples = [
        yaml.safe_load(block)
        for block in re.findall(r"```yaml\n(.*?)\n```", guide, re.S)
    ]
    deployment = CloudDeployment.model_validate(
        next(e for e in examples if e.get("kind") == "CloudDeployment")
    )
    graph = next(e for e in examples if "nodes" in e)
    plan = prepare_pipeline(graph, deployment)
    assert set(plan.tasks) == {"tokens", "count"}
    assert plan.tasks["count"].settings.timeout == 600
    single = prepare_procedure("provium_text_pipeline_example.TokenizeV1", deployment)
    assert single.targets == {"destination": "$nodes.procedure.destination"}
    for page in ("cloud.md", "cloud-testing.md"):
        for block in re.findall(
            r"```python\n(.*?)\n```", (ROOT / "docs/guides" / page).read_text(), re.S
        ):
            compile(block, page, "exec")


def test_published_iam_templates_render_without_account_specific_values():
    directory = ROOT / "packages/provium-cloud/infrastructure/aws/iam"
    trust = json.loads((directory / "trust.json").read_text())
    assert trust["Statement"][0]["Principal"] == {
        "Service": "cloudformation.amazonaws.com"
    }
    source = (directory / "permissions.json").read_text()
    assert not re.search(r"\d{12}", source)
    policy = json.loads(
        Template(source).substitute(
            ACCOUNT_ID="123456789012",
            REGION="us-east-1",
            STACK_PREFIX="provium-cloud-test",
        )
    )
    assert len(json.dumps(policy, separators=(",", ":"))) < 10240
    assert all("*" not in statement["Action"] for statement in policy["Statement"])
    statements = {s["Sid"]: s for s in policy["Statement"]}
    assert (
        statements["PassTestRoles"]["Resource"]
        == "arn:aws:iam::123456789012:role/provium-cloud-test-*"
    )
    assert statements["PassTestRoles"]["Condition"]["StringEquals"][
        "iam:PassedToService"
    ] == ["batch.amazonaws.com", "ecs-tasks.amazonaws.com"]
