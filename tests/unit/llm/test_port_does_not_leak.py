"""LangChain may be imported in exactly one directory, and this is what says so.

The `LlmProvider` port exists so that a LangChain breaking change touches one
file. That guarantee is one `from langchain_core...` away from being false, and
nothing else in the gate would notice: an import that leaks is not a test
failure, not a lint finding, and not an import-linter violation, because
import-linter's contract here is over first-party packages only.

Source text rather than imports of the modules themselves: a module that
imports LangChain lazily inside a function still leaks the types into its
signatures, and importing every module to inspect it would need every optional
extra installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src" / "redstring"

#: The one directory permitted to know LangChain exists.
ADAPTERS = SOURCE_ROOT / "llm" / "adapters"


def imported_modules(source: Path) -> set[str]:
    """Every module name `source` imports, by any syntax and at any depth."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=str(source))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module)
    return names


def python_files_outside_the_adapters() -> list[Path]:
    return sorted(
        path
        for path in SOURCE_ROOT.rglob("*.py")
        if ADAPTERS not in path.parents and "__pycache__" not in path.parts
    )


def test_the_walk_finds_the_library():
    """Guards the test itself: a wrong `SOURCE_ROOT` would pass vacuously.

    The check below is a search for something that should not be there, so it
    succeeds trivially when it searches nothing at all.
    """
    files = python_files_outside_the_adapters()

    assert len(files) > 50, f"expected the whole library under {SOURCE_ROOT}, found {len(files)}"
    assert SOURCE_ROOT / "ports" / "llm_provider.py" in files


def test_the_adapter_directory_really_does_import_langchain():
    """The other half of the same guard.

    If `ADAPTERS` pointed at the wrong directory, it would be excluded from
    nothing and the leak check would still pass -- while silently no longer
    exempting the one place the import belongs.
    """
    langchain_importers = {
        path.name
        for path in ADAPTERS.rglob("*.py")
        if any(name.startswith("langchain") for name in imported_modules(path))
    }

    assert "langchain.py" in langchain_importers


def test_no_module_outside_the_adapters_imports_langchain():
    leaks = {
        str(path.relative_to(SOURCE_ROOT)): sorted(
            name for name in imported_modules(path) if name.startswith("langchain")
        )
        for path in python_files_outside_the_adapters()
    }
    offenders = {path: names for path, names in leaks.items() if names}

    assert offenders == {}, (
        "LangChain must not be imported outside redstring/llm/adapters/; "
        f"found {offenders}. Put the dependency behind redstring.ports.llm_provider."
    )
