"""The store-adapter guide's port table must name every compliance suite.

`docs/how-to/implement-a-store-adapter.md` is the canonical procedure for a
third party implementing a port, and its table of ports is the only place that
says which shared suite proves an adapter correct. It shipped saying "the four
ports" while there were six, omitting `ChunkStore` and `EmbeddingProvider` --
so someone implementing a `ChunkStore`, reading the guide written for exactly
that task, would have concluded no suite existed for it and written bespoke
tests instead. That is `.claude/rules/recurring-defects.md` §1 with the guide
as the mechanism that caused it.

Meanwhile `.claude/rules/definition-of-done.md` carried a correct five-row
table of the same fact. One fact in two places with nothing failing when the
copies disagree is §2, and prose is where it always rots.

## Why a test rather than a careful edit

Correcting the guide fixes today. Nothing stops the seventh port, and nothing
stops a suite being deleted while its row survives -- the guide would then
send an implementer to a module that does not exist. This is
[ADR 0014](../../docs/adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)
applied to a document: a list that matches nothing passes silently, so it
needs a test that its entries still name something real.

Both directions are checked, because a one-directional check rots into a
passing one:

- a suite in `tests/compliance/` with no row fails (the original defect);
- a row naming a suite that no longer exists fails (the defect a correction
  alone would leave open).

## What it matches on, and what it deliberately does not

The obvious implementation greps the whole document for `chunk_store` and
passes on any mention. That is the failure shape CLAUDE.md's table is about --
an input on which the right document and a useless one agree, since a suite
named once in a passing sentence is not a suite named in the table an
implementer reads.

So the match is on **the fully qualified compliance class, inside a markdown
table row**: `tests.compliance.<module>.<Class>Compliance` on a line beginning
with `|`. That is exactly the cell an implementer copies into an `import`, it
survives any reformatting of the table's other columns, and a mention in prose
does not satisfy it.

The suite list is likewise **derived from the tree, not hand-kept**: every
module under `tests/compliance/` is parsed with `ast` and every top-level
class whose name ends in `Compliance` is a suite. A hand-kept list needs
updating by the same person who forgot the table. `strategies.py` and
`__init__.py` fall out on their own by declaring no such class, rather than
being blacklisted by name.

`LlmProvider` has no compliance suite and its row says so; a port without a
suite contributes nothing to match, which is why the port column is checked
separately.

## Broken on purpose before being believed

A gate whose happy path is "the string is there" is the kind CLAUDE.md warns
about, so each direction was watched failing: a fake
`tests/compliance/widget_store.py` declaring `WidgetStoreCompliance` (the
suite direction), and the `ChunkStore` row deleted from the guide (the
staleness direction, and the ports check with it).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

GUIDE = REPO_ROOT / "docs" / "how-to" / "implement-a-store-adapter.md"

COMPLIANCE_DIR = REPO_ROOT / "tests" / "compliance"

PORTS_DIR = REPO_ROOT / "src" / "redstring" / "ports"

#: A dotted path to a compliance class, as written in the guide's table.
#: Anchored on the package so a bare class name in prose does not match.
_SUITE_IN_TABLE = re.compile(r"tests\.compliance\.[a-z_]+\.[A-Za-z]+Compliance")

#: The port's Protocol name in a backticked cell.
_CODE_SPAN = re.compile(r"`([^`]+)`")


def compliance_suites() -> set[str]:
    """Every `<module>.<Class>` under `tests/compliance/`, found by parsing.

    Derived rather than listed: a new suite is included the day it is written,
    which is the whole point. Modules declaring no `*Compliance` class -- so
    `strategies.py` and `__init__.py` -- contribute nothing without being
    named here, so deleting one is not a false failure.
    """
    found: set[str] = set()
    for module in sorted(COMPLIANCE_DIR.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name.endswith("Compliance"):
                found.add(f"{module.stem}.{node.name}")
    return found


def suites_named_in_the_guide() -> set[str]:
    """Every compliance class the guide names *in a table row*.

    Only lines starting with `|` are considered, so a suite mentioned in a
    paragraph does not satisfy the gate. The returned form is
    `<module>.<Class>`, matching `compliance_suites()`.
    """
    named: set[str] = set()
    for line in GUIDE.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        for match in _SUITE_IN_TABLE.findall(line):
            _, _, tail = match.partition("tests.compliance.")
            named.add(tail)
    return named


def port_protocols() -> set[str]:
    """Every runtime-checkable Protocol in `src/redstring/ports/` that is a port.

    A port module may declare several capability protocols composed into one
    (`GraphStore` is five, `ChunkStore` is four -- ADR 0016), and an adapter
    implements the composed name. That is the one an implementer looks up, so
    it is the one the guide must name: the class whose name is the module's,
    in PascalCase.
    """
    wanted: set[str] = set()
    for module in sorted(PORTS_DIR.glob("*.py")):
        if module.stem == "__init__":
            continue
        expected = "".join(part.title() for part in module.stem.split("_"))
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == expected:
                wanted.add(node.name)
    return wanted


def ports_named_in_the_guide() -> set[str]:
    """Every backticked name appearing in any of the guide's table cells."""
    named: set[str] = set()
    for line in GUIDE.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        named.update(_CODE_SPAN.findall(line))
    return named


@pytest.mark.unit
def test_the_detectors_find_something() -> None:
    """The guard for the guard.

    A comparison between two empty sets passes, and would be
    indistinguishable from a working gate -- a wrong `COMPLIANCE_DIR`, a
    renamed guide, or a table reformatted out of `|` rows would all read as
    green. So each half is asserted non-trivial before the halves are compared.
    """
    assert GUIDE.is_file(), f"{GUIDE} is missing; the gate below would pass over nothing"
    assert len(compliance_suites()) >= 5, compliance_suites()
    assert len(suites_named_in_the_guide()) >= 5, suites_named_in_the_guide()
    assert len(port_protocols()) >= 6, port_protocols()


@pytest.mark.unit
def test_every_compliance_suite_is_named_in_the_guide() -> None:
    """A suite nobody documented fails here rather than going unmentioned."""
    undocumented = compliance_suites() - suites_named_in_the_guide()

    assert not undocumented, (
        f"{GUIDE.relative_to(REPO_ROOT)} does not name these compliance suites "
        f"in its port table: {sorted(undocumented)}. An implementer reading the "
        f"guide written for their task would conclude no shared suite exists "
        f"and write bespoke tests, which is the divergence the suite prevents. "
        f"Add a row naming `tests.compliance.<module>.<Class>`."
    )


@pytest.mark.unit
def test_the_guide_names_no_compliance_suite_that_has_gone() -> None:
    """The staleness half: a row outliving its suite sends readers nowhere.

    Without this, correcting the table today buys nothing tomorrow -- ADR 0014
    exactly, in markdown instead of `pyproject.toml`.
    """
    stale = suites_named_in_the_guide() - compliance_suites()

    assert not stale, (
        f"{GUIDE.relative_to(REPO_ROOT)} names compliance suites that do not "
        f"exist under tests/compliance/: {sorted(stale)}. Either the suite was "
        f"renamed or deleted and the row was not, or the row has a typo -- "
        f"both send an implementer to an import that fails."
    )


@pytest.mark.unit
def test_every_port_is_named_in_the_guide() -> None:
    """The finding this module was written for: the count, not just the suites.

    `LlmProvider` has no suite, so the suite checks above cannot see it going
    missing from the guide. This is the check that can.
    """
    unmentioned = port_protocols() - ports_named_in_the_guide()

    assert not unmentioned, (
        f"{GUIDE.relative_to(REPO_ROOT)} does not name these ports in any of "
        f"its tables: {sorted(unmentioned)}. The guide is the canonical "
        f"procedure for implementing one, and a port absent from it reads as a "
        f"port that does not exist."
    )
