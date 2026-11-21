import base64
from pathlib import Path
from unittest.mock import MagicMock

from infrastructure.aws.image import build_image, prepare_context


def test_build_context_only_includes_runtime_and_package_sources(tmp_path):
    repo = Path(__file__).resolve().parents[4]
    prepare_context(repo, tmp_path)
    assert (tmp_path / "packages/provium-cloud/src/provium_cloud/__init__.py").is_file()
    assert (tmp_path / "runtime/live_test.py").is_file()
    assert (tmp_path / "Dockerfile").is_file()
    assert not (tmp_path / ".git").exists()
    assert not (tmp_path / "packages/provium-cloud/test").exists()
    assert not list(tmp_path.rglob(".env"))
    assert not list(tmp_path.rglob("__pycache__"))
    assert not list(tmp_path.rglob("*.egg-info"))


def test_ecr_password_uses_stdin_and_isolated_config(tmp_path, monkeypatch):
    ecr = MagicMock()
    ecr.get_authorization_token.return_value = {
        "authorizationData": [
            {"authorizationToken": base64.b64encode(b"AWS:secret").decode()}
        ]
    }
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        if "login" in command:
            assert kwargs["input"] == "secret"
            assert "secret" not in " ".join(command)
            assert Path(kwargs["env"]["DOCKER_CONFIG"]).is_dir()

    monkeypatch.setattr("infrastructure.aws.image.subprocess.run", run)
    monkeypatch.setattr("infrastructure.aws.image.prepare_context", lambda *args: None)
    build_image(ecr, "123456789012.dkr.ecr.us-east-1.amazonaws.com/test", tmp_path)
    assert ["--platform", "linux/amd64"] == commands[1][0][3:5]
    assert commands[-1][0][:2] == ["docker", "push"]
    assert not Path(commands[0][1]["env"]["DOCKER_CONFIG"]).exists()


def test_isolated_login_preserves_selected_docker_context(tmp_path, monkeypatch):
    import json

    original = tmp_path / "original"
    original.mkdir()
    (original / "config.json").write_text(
        json.dumps(
            {
                "currentContext": "desktop-linux",
                "auths": {"private": {"auth": "unrelated"}},
            }
        )
    )
    context = original / "contexts" / "meta"
    context.mkdir(parents=True)
    (context / "meta.json").write_text("{}")
    monkeypatch.setenv("DOCKER_CONFIG", str(original))
    ecr = MagicMock()
    ecr.get_authorization_token.return_value = {
        "authorizationData": [
            {"authorizationToken": base64.b64encode(b"AWS:secret").decode()}
        ]
    }

    def execute(command, **kwargs):
        if "login" in command or "push" in command:
            config = Path(kwargs["env"]["DOCKER_CONFIG"])
            assert json.loads((config / "config.json").read_text()) == {
                "currentContext": "desktop-linux"
            }
            assert (config / "contexts/meta/meta.json").is_file()
        else:
            assert kwargs["env"]["DOCKER_CONFIG"] == str(original)

    monkeypatch.setattr("infrastructure.aws.image.subprocess.run", execute)
    monkeypatch.setattr("infrastructure.aws.image.prepare_context", lambda *args: None)
    build_image(ecr, "123456789012.dkr.ecr.us-east-1.amazonaws.com/test", tmp_path)
