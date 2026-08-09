"""A library reads no environment. Its caller does, and passes the values in.

This is the rule that killed `redstring.config` (BACKLOG B56). A 287-line
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

SRC = Path(__file__).resolve().parents[2] / "src" / "redstring"

#: Names whose appearance means a module is reaching for the process
#: environment. `dotenv` is here because it is the same act with a file in
#: front of it.
BANNED_MODULES = frozenset({"pydantic_settings", "dotenv", "configparser"})

#: Attribute/function names that read the environment however they are
#: imported: `os.getenv(...)`, `from os import environ`, `os.environ[...]`.
BANNED_NAMES = frozenset({"getenv", "environ"})


#: The only modules permitted to read the environment, each with the reason.
#:
#: **Both are compliance suites, not library code**, and the distinction is
#: the one B56 was about. The rule exists because a library that reads the
#: environment cannot be configured by its caller: two callers in one process
#: cannot disagree, and configuring it means mutating `os.environ`. Neither
#: consequence applies to a *test-run* lever read by a suite the caller
#: invokes through pytest -- the caller there is a pytest invocation, and
#: setting a variable for one is the normal way to configure it.
#:
#: `KG_COMPLIANCE_MAX_EXAMPLES` also cannot be anything else as the shared
#: `settings()` is written: it is read at import, before any subclass body
#: runs, so it is per-run and not per-adapter. That is BACKLOG B10h, which
#: records what would have to change to make it a class attribute -- and why
#: an explicit `settings(max_examples=...)` on a subclass is the wrong fix
#: (it outranks every hypothesis profile).
#:
#: Two tests below guard this list in both directions: an entry naming a file
#: that no longer exists fails, and so does an entry whose file has stopped
#: reading the environment. An exemption that has outlived its cause is the
#: shape ADR 0014 is about.
ENVIRONMENT_EXEMPT = {
    "testing/graph_store.py": "KG_COMPLIANCE_MAX_EXAMPLES, a per-run hypothesis budget",
    "testing/vector_store.py": "KG_COMPLIANCE_MAX_EXAMPLES, a per-run hypothesis budget",
}


def _modules() -> list[Path]:
    paths = sorted(SRC.rglob("*.py"))
    # A bounded loop's worth of paranoia: if the glob silently resolved to an
    # empty tree (a moved package, a renamed src layout), every assertion
    # below would pass vacuously and this file would report success while
    # checking nothing.
    assert len(paths) > 50, f"expected the whole package under {SRC}, found {len(paths)} modules"
    return paths


def _offences(path: Path) -> list[str]:
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

    return offences


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_module_does_not_read_the_environment(path: Path) -> None:
    relative = path.relative_to(SRC).as_posix()
    if relative in ENVIRONMENT_EXEMPT:
        pytest.skip(f"exempt: {ENVIRONMENT_EXEMPT[relative]}")

    assert not _offences(path), (
        f"{relative} reads the environment via {sorted(set(_offences(path)))}. "
        f"A library takes its configuration through constructor arguments; the "
        f"caller is what reads the environment. See BACKLOG B56."
    )


@pytest.mark.parametrize("relative", sorted(ENVIRONMENT_EXEMPT))
def test_an_exempt_module_still_exists(relative: str) -> None:
    """An exemption naming a deleted file passes silently. ADR 0014."""
    assert (SRC / relative).is_file(), (
        f"{relative} is exempt from the environment gate and does not exist. Delete the entry."
    )


@pytest.mark.parametrize("relative", sorted(ENVIRONMENT_EXEMPT))
def test_an_exempt_module_still_reads_the_environment(relative: str) -> None:
    """The other direction, and the one an exemption list usually lacks.

    An entry whose module has stopped reading the environment is an exemption
    over an empty set: it excludes nothing, and it will silently absorb the
    *next* environment read someone adds to that file. Requiring the offence
    to still be there is what makes the list shrink on its own.
    """
    assert _offences(SRC / relative), (
        f"{relative} no longer reads the environment, so its exemption is "
        f"doing nothing except hiding the next one. Delete the entry."
    )


@pytest.mark.parametrize("relative", sorted(ENVIRONMENT_EXEMPT))
def test_an_exemption_carries_a_reason(relative: str) -> None:
    assert ENVIRONMENT_EXEMPT[relative].strip(), f"{relative} is exempt with no reason given"
