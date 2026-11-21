from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2] / "infrastructure" / "aws"


def test_stack_is_disposable_and_database_is_private():
    template = yaml.safe_load((ROOT / "stack.yaml").read_text())
    resources = template["Resources"]
    database = resources["Database"]
    assert database["DeletionPolicy"] == "Delete"
    assert database["Properties"]["PubliclyAccessible"] is False
    assert database["Properties"]["StorageEncrypted"] is True
    assert database["Properties"]["ManageMasterUserPassword"] is True
    assert "MasterUserPassword" not in database["Properties"]
    assert resources["Repository"]["Properties"]["EmptyOnDelete"] is True
    bucket = resources["Artifacts"]["Properties"]
    assert all(bucket["PublicAccessBlockConfiguration"].values())
    assert bucket["BucketEncryption"]
    assert bucket["VersioningConfiguration"]["Status"] == "Enabled"
    assert (
        resources["ArtifactsPolicy"]["Properties"]["PolicyDocument"]["Statement"][0][
            "Effect"
        ]
        == "Deny"
    )
    assert resources["DatabaseSecurityGroup"]["Properties"]["SecurityGroupIngress"] == [
        {
            "IpProtocol": "tcp",
            "FromPort": 5432,
            "ToPort": 5432,
            "SourceSecurityGroupId": {"Ref": "TaskSecurityGroup"},
        }
    ]
    assert resources["Compute"]["Properties"]["ComputeResources"]["Type"] == "FARGATE"
    assert resources["Compute"]["Properties"]["ComputeResources"]["MaxvCpus"] == 4
    assert not any(r["Type"] == "AWS::EC2::NatGateway" for r in resources.values())
    for name in ("Worker", "Driver"):
        container = resources[name]["Properties"]["ContainerProperties"]
        assert container["NetworkConfiguration"]["AssignPublicIp"] == "ENABLED"
        assert container["RuntimePlatform"]["CpuArchitecture"] == "X86_64"
        assert resources[name]["Properties"]["RetryStrategy"]["Attempts"] == 1
    assert "com.aws.cloudformation.Context" in database["Metadata"]


def test_batch_names_match_stack_scoped_iam_permissions():
    resources = yaml.safe_load((ROOT / "stack.yaml").read_text())["Resources"]
    for name, field in (
        ("Compute", "ComputeEnvironmentName"),
        ("Queue", "JobQueueName"),
        ("Worker", "JobDefinitionName"),
        ("Driver", "JobDefinitionName"),
    ):
        assert resources[name]["Properties"][field] == {
            "Fn::Sub": "${AWS::StackName}-" + name.lower()
        }
