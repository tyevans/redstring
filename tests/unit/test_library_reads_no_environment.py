"""A library reads no environment. Its caller does, and passes the values in.

This is the rule that killed `kg_builder.config` (BACKLOG B56). A 287-line
`Settings` object survived nine slices of deletion because each slice could
only argue about the keys it had just orphaned, and the object itself was
never the subject. The rule replaces that argument with a check.

Why it is a test rather than a review habit: a process-wide settings object is
*convenient* at every individual call site. `os.getenv` inside a registry is
one line and reads as a sensible default. The cost only shows up somewhere
else -- two callers in one process cannot disagree, a caller cannot configure
the library at all without mutating its own environment, and an import-time
read turns a missing key into an `ImportError` in a module that has nothing to
do with the key.

Scanned with `ast` rather than `grep`, so the string `"os.environ"` in a
docstring (this one, for instance) does not fail the gate and a real call
spelled `environ.get(...)` after `from os import environ` does not slip past.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "kg_builder"

#: Names whose appearance means a module is reaching for the process
#: environment. `dotenv` is here because it is the same act with a file in
#: front of it.
BANNED_MODULES = frozenset({"pydantic_settings", "dotenv", "configparser"})

#: Attribute/function names that read the environment however they are
#: imported: `os.getenv(...)`, `from os import environ`, `os.environ[...]`.
BANNED_NAMES = frozenset({"getenv", "environ"})


def _modules() -> list[Path]:
    paths = sorted(SRC.rglob("*.py"))
    # A bounded loop's worth of paranoia: if the glob silently resolved to an
    # empty tree (a moved package, a renamed src layout), every assertion
    # below would pass vacuously and this file would report success while
    # checking nothing.
    assert len(paths) > 50, f"expected the whole package under {SRC}, found {len(paths)} modules"
    return paths


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_module_does_not_read_the_environment(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offences = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offences += [a.name for a in node.names if a.name.split(".")[0] in BANNED_MODULES]
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_MODULES:
                offences.append(node.module or "")
            if root == "os":
                offences += [a.name for a in node.names if a.name in BANNED_NAMES]
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_NAMES:
            offences.append(f"<expr>.{node.attr}")

    assert not offences, (
        f"{path.relative_to(SRC)} reads the environment via {sorted(set(offences))}. "
        f"A library takes its configuration through constructor arguments; the "
        f"caller is what reads the environment. See BACKLOG B56."
    )
