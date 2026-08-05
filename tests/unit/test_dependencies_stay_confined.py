"""Each third-party client lives in one directory, and this is what says so.

Every port here exists so that a breaking change in someone else's library
touches one file. That guarantee is a single `import` away from being false,
and **nothing else in the gate would notice**: a leaked import is not a test
failure, not a lint finding, and not an `import-linter` violation, because the
architecture contract is over first-party packages only (CLAUDE.md says so
explicitly).

This file started life under `tests/unit/llm/`, named for the LLM port, and
covered `langchain` alone. Three other libraries were confined by convention with
nothing enforcing it -- `neo4j`, `asyncpg` and `redis`, each correctly placed
and each one commit from not being. That is the shape `recurring-defects.md`
calls inert: a rule that holds only because nobody has broken it yet is
indistinguishable from no rule, right up until the day it differs.

Generalising it was prompted by `eventsource-py` 0.11.0, which widened its own
Tier 0 contract from `sqlalchemy` to six drivers for exactly this reason --
`redis` had been an optional extra there since 0.5.0 with nothing asserting its
absence from the core surface.

## Source text rather than importing the modules

A module that imports its client lazily inside a function still leaks the
types into its signatures, and importing every module to inspect it would need
every optional extra installed -- which is the environment CLAUDE.md records
losing a whole mutation run to.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "redstring"


@dataclass(frozen=True)
class Confinement:
    """One library, the one directory allowed to know it exists, and the port.

    `port` is not used by any assertion -- it is in the failure message,
    because the useful thing to tell someone who has just leaked an import is
    where the seam they should have used lives.
    """

    packages: tuple[str, ...]
    directory: str
    port: str

    @property
    def permitted(self) -> Path:
        return SOURCE_ROOT / Path(self.directory)

    def __str__(self) -> str:
        return f"{'/'.join(self.packages)} -> {self.directory}"


#: Every third-party client the architecture deliberately keeps in one place.
#:
#: `openai` rides with `langchain` because `langchain.py` catches two of its
#: exception types to classify finish reasons; it is the same seam, not a
#: second one.
CONFINEMENTS = (
    Confinement(
        packages=("langchain", "openai"),
        directory="llm/adapters",
        port="redstring.ports.llm_provider / redstring.ports.embedding_provider",
    ),
    Confinement(
        packages=("neo4j",),
        directory="graph/adapters",
        port="redstring.ports.graph_store",
    ),
    Confinement(
        packages=("asyncpg",),
        directory="vector/adapters",
        port="redstring.ports.vector_store",
    ),
    Confinement(
        packages=("redis",),
        directory="llm/cache",
        port="redstring.ports.cache",
    ),
)


def imported_modules(source: Path) -> set[str]:
    """Every module name `source` imports, by any syntax and at any depth."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=str(source))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module)
    return names


def belongs_to(module: str, package: str) -> bool:
    """Whether `module` comes from the distribution `package` names.

    Matching is on the **top-level component**, plus the `package_*` family,
    rather than a bare `module.startswith(package)`. Both halves are load-bearing
    and neither is obvious:

    - `langchain` ships as `langchain`, `langchain_core` and `langchain_openai`,
      so the family has to count.
    - a bare prefix makes `redis` match a module named `redistribute`, and
      `neo4j` match anything beginning with those five characters. The failure
      that causes is the bad kind: a *false* leak report, in a test whose whole
      job is to be believed.
    """
    top = module.split(".")[0]
    return top == package or top.startswith(f"{package}_")


def python_files_under(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def files_outside(permitted: Path) -> list[Path]:
    return [path for path in python_files_under(SOURCE_ROOT) if permitted not in path.parents]


def test_the_walk_finds_the_library():
    """Guards every check below: a wrong `SOURCE_ROOT` would pass vacuously.

    The leak checks are searches for something that should not be there, so
    they succeed trivially when they search nothing at all.
    """
    files = python_files_under(SOURCE_ROOT)

    assert len(files) > 50, f"expected the whole library under {SOURCE_ROOT}, found {len(files)}"
    assert SOURCE_ROOT / "ports" / "llm_provider.py" in files


@pytest.mark.parametrize("confinement", CONFINEMENTS, ids=str)
def test_the_permitted_directory_exists(confinement: Confinement):
    """A directory that is not there excludes nothing, and the leak check
    below would still pass -- while silently no longer exempting the one place
    the import belongs."""
    assert confinement.permitted.is_dir(), (
        f"{confinement.directory} does not exist, so the exemption for "
        f"{confinement.packages} matches nothing"
    )


@pytest.mark.parametrize("confinement", CONFINEMENTS, ids=str)
def test_the_permitted_directory_really_does_import_it(confinement: Confinement):
    """The staleness guard CLAUDE.md requires of every exemption list.

    An entry naming a library nobody imports any more passes forever and
    protects nothing. This is the check that turns removing a dependency into
    a visible decision rather than a silently-inert rule -- the same reason
    `pyproject.toml`'s per-file ignores are now empty rather than carrying
    deleted paths.
    """
    importers = {
        path.relative_to(SOURCE_ROOT)
        for path in python_files_under(confinement.permitted)
        if any(
            belongs_to(module, package)
            for module in imported_modules(path)
            for package in confinement.packages
        )
    }

    assert importers, (
        f"nothing under {confinement.directory} imports any of "
        f"{confinement.packages}. Either the adapter moved -- in which case "
        f"this entry now exempts the wrong directory -- or the dependency is "
        f"gone and the entry should be deleted."
    )


@pytest.mark.parametrize("confinement", CONFINEMENTS, ids=str)
def test_nothing_outside_the_permitted_directory_imports_it(confinement: Confinement):
    offenders = {
        str(path.relative_to(SOURCE_ROOT)): sorted(
            module
            for module in imported_modules(path)
            for package in confinement.packages
            if belongs_to(module, package)
        )
        for path in files_outside(confinement.permitted)
    }
    offenders = {path: modules for path, modules in offenders.items() if modules}

    assert offenders == {}, (
        f"{'/'.join(confinement.packages)} must not be imported outside "
        f"redstring/{confinement.directory}/; found {offenders}. Put the "
        f"dependency behind {confinement.port}."
    )


def test_belongs_to_does_not_match_by_bare_prefix():
    """Pins the rule the matcher exists for, because getting it wrong is silent
    in one direction and noisy in the other.

    Without the `_` family clause, `langchain_core` is not recognised and the
    leak check under-reports. With a bare `startswith`, `redistribute` reads as
    a `redis` import and the check over-reports.
    """
    assert belongs_to("langchain_core.messages", "langchain")
    assert belongs_to("langchain", "langchain")
    assert belongs_to("neo4j.graph", "neo4j")

    assert not belongs_to("redistribute", "redis")
    assert not belongs_to("neo4jsonschema", "neo4j")
    assert not belongs_to("redstring.ports.cache", "redis")
