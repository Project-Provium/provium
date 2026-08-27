import json
import re
from pathlib import Path

_NOTEBOOKS = (
    "00-orientation.ipynb",
    "01-definitions-and-compilation.ipynb",
    "02-inputs-and-runs.ipynb",
    "03-dispatch-and-execution.ipynb",
    "04-artifacts-cache-and-gc.ipynb",
    "05-storage-recovery-and-concurrency.ipynb",
    "06-cli-workflows.ipynb",
    "07-extension-points-and-testing.ipynb",
)

_DOCUMENTS = (
    "index.md",
    "concepts.md",
    "architecture.md",
    "definitions-and-compilation.md",
    "inputs-and-runs.md",
    "execution.md",
    "artifacts.md",
    "storage-and-recovery.md",
    "cli.md",
    "extensions.md",
    "operations.md",
    "testing.md",
    "api-reference.md",
)


def test_documentation_covers_guides_navigation_and_every_runtime_module() -> None:
    package = Path(__file__).resolve().parents[1]
    docs = package / "docs"
    readme = (package / "README.md").read_text(encoding="utf-8")

    for name in _DOCUMENTS:
        page = docs / name
        assert page.is_file(), f"missing documentation page: {name}"
        content = page.read_text(encoding="utf-8")
        assert content.startswith("# "), f"documentation page needs an H1: {name}"
        assert f"docs/{name}" in readme, f"README does not link {name}"

    index = (docs / "index.md").read_text(encoding="utf-8")
    for name in _DOCUMENTS[1:]:
        assert f"({name})" in index, f"documentation index does not link {name}"

    module_reference = (docs / "api-reference.md").read_text(encoding="utf-8")
    source = package / "src" / "provium_pipeline"
    modules: list[str] = []
    for path in source.rglob("*.py"):
        relative = path.relative_to(package / "src").with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        modules.append(".".join(parts))
    for module in sorted(set(modules)):
        assert f"`{module}`" in module_reference, f"undocumented module: {module}"


def test_relative_documentation_links_resolve() -> None:
    package = Path(__file__).resolve().parents[1]
    pages = [
        package / "README.md",
        package / "notebooks" / "README.md",
        *(package / "docs").glob("*.md"),
    ]

    for page in pages:
        content = page.read_text(encoding="utf-8")
        for raw_target in re.findall(r"\[[^]]*\]\(([^)]+)\)", content):
            target = raw_target.strip().split("#", maxsplit=1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (page.parent / target).resolve()
            assert resolved.exists(), f"broken link in {page.name}: {raw_target}"


def test_tutorial_notebooks_are_ordered_linked_and_executable() -> None:
    package = Path(__file__).resolve().parents[1]
    notebooks = package / "notebooks"
    index = (notebooks / "README.md").read_text(encoding="utf-8")
    package_readme = (package / "README.md").read_text(encoding="utf-8")

    assert "notebooks/README.md" in package_readme
    for position, name in enumerate(_NOTEBOOKS):
        path = notebooks / name
        assert path.is_file(), f"missing tutorial notebook: {name}"
        assert f"({name})" in index, f"notebook index does not link {name}"
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["nbformat"] == 4
        assert document["metadata"]["kernelspec"]["language"] == "python"
        cells = document["cells"]
        assert cells[0]["cell_type"] == "markdown"
        assert cells[0]["source"][0].startswith(f"# {position}.")
        assert any(cell["cell_type"] == "code" for cell in cells)

        namespace: dict[str, object] = {"__name__": "__notebook__"}
        for cell in cells:
            source = "".join(cell["source"])
            if cell["cell_type"] == "markdown":
                for raw_target in re.findall(r"\[[^]]*\]\(([^)]+)\)", source):
                    target = raw_target.strip().split("#", maxsplit=1)[0]
                    if (
                        target
                        and "://" not in target
                        and not target.startswith("mailto:")
                    ):
                        resolved = (path.parent / target).resolve()
                        assert resolved.exists(), f"broken link in {name}: {raw_target}"
            if cell["cell_type"] == "code":
                exec(compile(source, str(path), "exec"), namespace)


def test_source_layout_uses_singular_domain_packages() -> None:
    package = Path(__file__).resolve().parents[1]
    source = package / "src" / "provium_pipeline"
    domains = (
        "input",
        "run",
        "dispatch",
        "execution",
        "cache",
        "lifecycle",
        "plugin",
        "cli",
    )
    for domain in domains:
        assert (source / domain / "__init__.py").is_file(), (
            f"missing domain package: {domain}"
        )

    forbidden_prefixes = ("input_", "inputs.py", "sqlite_input_sets")
    flat_modules = tuple(path.name for path in source.glob("*.py"))
    for prefix in forbidden_prefixes:
        assert not any(name.startswith(prefix) for name in flat_modules), (
            f"flat modules remain for {prefix}: {flat_modules}"
        )
