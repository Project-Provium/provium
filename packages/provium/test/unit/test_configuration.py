"""Application configuration discovery and layering."""

from pathlib import Path

import pytest

from provium.configuration import discover_configuration, load_configuration


def write(directory: Path, name: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content)
    return path


def test_layers_are_sorted_and_project_overrides_user(tmp_path: Path) -> None:
    user = tmp_path / "user"
    project = tmp_path / "project"
    first = write(
        user,
        "10-base.toml",
        '[pipeline]\nstore = "user"\nworkers = 2\nlabels = ["old"]\n',
    )
    second = write(user, "20-extra.toml", "[pipeline]\nworkers = 4\n")
    local = write(
        project / ".provium",
        "config.toml",
        '[pipeline]\nstore = "project"\nlabels = ["new"]\n',
    )
    write(project / ".provium" / "artifacts", "ignored.toml", "invalid")
    write(user, "ignored.txt", "invalid")
    (user / "directory.toml").mkdir()
    child = project / "src"
    child.mkdir()
    assert discover_configuration(user_directory=user, start_directory=child) == (
        first,
        second,
        local,
    )
    assert load_configuration(user_directory=user, start_directory=child) == {
        "pipeline": {"store": "project", "workers": 4, "labels": ["new"]}
    }


def test_nearest_project_directory_is_boundary(tmp_path: Path) -> None:
    write(tmp_path / ".provium", "config.toml", 'value = "parent"')
    nested = tmp_path / "nested"
    (nested / ".provium").mkdir(parents=True)
    assert (
        load_configuration(user_directory=tmp_path / "absent", start_directory=nested)
        == {}
    )


def test_defaults_use_home_and_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    write(tmp_path / ".config" / "provium", "config.toml", "value = 1")
    assert load_configuration() == {"value": 1}


def test_missing_directories_are_empty(tmp_path: Path) -> None:
    assert (
        load_configuration(
            user_directory=tmp_path / "missing", start_directory=tmp_path
        )
        == {}
    )


def test_scalar_and_table_replace_each_other(tmp_path: Path) -> None:
    write(tmp_path, "a.toml", "first = 1\n[second]\nvalue = 2\n")
    write(tmp_path, "b.toml", "second = 3\n[first]\nvalue = 4\n")
    assert load_configuration(user_directory=tmp_path, start_directory=tmp_path) == {
        "first": {"value": 4},
        "second": 3,
    }


def test_invalid_toml_identifies_file(tmp_path: Path) -> None:
    path = write(tmp_path, "broken.toml", "[broken")
    with pytest.raises(ValueError, match="broken.toml"):
        load_configuration(user_directory=tmp_path, start_directory=tmp_path)
    assert path.exists()


def test_configuration_is_reloaded(tmp_path: Path) -> None:
    path = write(tmp_path, "config.toml", "value = 1")
    assert load_configuration(user_directory=tmp_path, start_directory=tmp_path) == {
        "value": 1
    }
    path.write_text("value = 2")
    assert load_configuration(user_directory=tmp_path, start_directory=tmp_path) == {
        "value": 2
    }
