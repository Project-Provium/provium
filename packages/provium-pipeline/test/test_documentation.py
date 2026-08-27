import re
from pathlib import Path

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
    pages = [package / "README.md", *(package / "docs").glob("*.md")]

    for page in pages:
        content = page.read_text(encoding="utf-8")
        for raw_target in re.findall(r"\[[^]]*\]\(([^)]+)\)", content):
            target = raw_target.strip().split("#", maxsplit=1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (page.parent / target).resolve()
            assert resolved.exists(), f"broken link in {page.name}: {raw_target}"
