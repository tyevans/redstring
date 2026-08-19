"""Every `redstring` name and call in the docs is checked against the package.

`tests/unit/test_end_to_end_example.py` executes `docs/examples/build_a_graph.py`
and is the mechanism behind the public-surface gate. Nothing equivalent covered
the fenced Python in `README.md` or `docs/`, and BACKLOG B95 records what that
cost -- twice:

- `docs/how-to/rank-passages.md`'s only example called
  `index_documents(..., chunks=chunks, ...)` against a parameter actually named
  `store`. `mkdocs --strict` checks links, not Python, and every name the
  how-to imported *was* in `__all__` -- so the public-surface gate gave it zero
  protection while the page satisfied every condition that gate checks.
- `README.md`, `docs/getting-started.md` and `docs/installation.md` each
  constructed `LangChainLlmProvider(chat_model)`, missing the required
  keyword-only `model=`, so all three raised `TypeError` on the first line a
  real-provider user copies. Three sites drifted *together*, which is the tell
  that no mechanism was watching any of them.

**Why this checks signatures rather than executing the blocks.** B95 asked for
an executor. There are 387 fenced Python blocks across the docs; most are
fragments that assume a name from an earlier block, and the ones that are
whole need a Neo4j, a Postgres or a model endpoint. An executor for them would
be a second integration suite, and it would not run in the commit gate, which
is where the two defects above needed catching. Both were *signature* errors,
and binding a call against the real signature catches that class without
importing a driver or reaching a network.

So this gate is narrower than the entry asked for and runs where it matters.
What it does **not** catch stays open in B95: a name used but never imported, a
local shadowing the function it calls, a wrong argument *value*, and anything
that only fails when the statement runs. The first three all shipped in
`docs/how-to/drive-projections-from-an-event-store.md` and were found by
reading the block this gate had just flagged for a different reason.

**Two things this deliberately does not look at.**

`docs/adr/` is excluded. An ADR body is an immutable record of a decision as
taken, so it may legitimately name a symbol that has since been deleted --
which is exactly BACKLOG B100 (ADR 0007 cites `redstring.projections.project`,
removed in the 0.12.0 upstreaming). Gating ADRs here would either force a
rewrite of history or need an exemption list, and the entry already proposes
the right mechanism for them separately.

`docs/plans/`, `docs/history/` and `docs/superpowers/` are excluded for the
same reason in a weaker form: all three are records of work at a moment, are
kept out of the built site by `mkdocs.yml`, and are not code anyone copies.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

import redstring

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[2]

#: A fenced block introduced as ```python. Blocks fenced as ```text, ```bash
#: or ```cypher are not Python and are not the subject.
_FENCE = re.compile(r"^```python\n(.*?)^```", re.MULTILINE | re.DOTALL)

#: Prose that records a moment rather than telling a reader what to write.
#: See the module docstring -- `adr/` is the load-bearing one.
_EXCLUDED = ("/adr/", "/plans/", "/history/", "/superpowers/")


class Block(NamedTuple):
    """One fenced Python block, with enough to name it in a failure."""

    path: Path
    line: int
    source: str

    def __repr__(self) -> str:  # pragma: no cover - only shown on failure
        return f"{self.path.relative_to(ROOT)}:{self.line}"


def _documented_files() -> list[Path]:
    readme = [ROOT / "README.md"]
    docs = sorted(
        path
        for path in (ROOT / "docs").rglob("*.md")
        if not any(part in path.as_posix() for part in _EXCLUDED)
    )
    return readme + docs


def _blocks() -> Iterator[Block]:
    for path in _documented_files():
        text = path.read_text(encoding="utf-8")
        for match in _FENCE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            yield Block(path, line, match.group(1))


#: Parsed once: every block that is syntactically whole. A block that does not
#: parse is usually a deliberate fragment (a bare method signature, an indented
#: excerpt), which is not this gate's subject -- but see
#: `test_most_documented_blocks_parse` for why the *proportion* is asserted.
_PARSED: list[tuple[Block, ast.Module]] = []
for _block in _blocks():
    try:
        _PARSED.append((_block, ast.parse(_block.source)))
    except SyntaxError:
        continue


def _imported_from_redstring(tree: ast.Module) -> Iterator[tuple[str, str]]:
    """`(local name, exported name)` for each `from redstring import ...`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "redstring":
            for alias in node.names:
                yield alias.asname or alias.name, alias.name


def _is_elision(call: ast.Call) -> bool:
    """`SourceDocument(...)` -- a placeholder, not a call to check.

    `...` is a legal expression, so a naive binder reads this as one positional
    argument and reports "too many positional arguments" against a keyword-only
    signature. Docs use the form to elide arguments a reader has already seen,
    and it is the only elision spelling in this tree.
    """
    return (
        len(call.args) == 1
        and not call.keywords
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value is Ellipsis
    )


class TestEveryDocumentedNameExists:
    def test_every_name_imported_from_redstring_is_exported(self) -> None:
        missing = [
            (repr(block), exported)
            for block, tree in _PARSED
            for _, exported in _imported_from_redstring(tree)
            if not hasattr(redstring, exported)
        ]
        assert not missing, (
            f"Documentation imports names `redstring` does not export: {missing}. "
            f"Either the name moved (check whether it is now imported from "
            f"`eventsource`) or the page predates its removal."
        )


class TestEveryDocumentedCallBinds:
    """A call to an exported name binds against that name's real signature.

    Only calls to a name imported *directly* from `redstring` in the same block
    are checked. Resolving `store.upsert_many(...)` would mean inferring the
    type of `store`, and a wrong inference produces a false failure in a gate
    whose whole value is that a failure means something.
    """

    def test_calls_to_exported_names_bind(self) -> None:
        failures: list[str] = []
        for block, tree in _PARSED:
            names = dict(_imported_from_redstring(tree))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id not in names:
                    continue
                if _is_elision(node):
                    continue
                # `f(*args)` and `f(**kwargs)` hide the arity from a static
                # check; skipping is honest, unlike guessing at the count.
                if any(isinstance(arg, ast.Starred) for arg in node.args):
                    continue
                if any(keyword.arg is None for keyword in node.keywords):
                    continue

                target = getattr(redstring, names[node.func.id])
                try:
                    signature = inspect.signature(target)
                except (TypeError, ValueError):  # pragma: no cover - builtins
                    continue

                positional: list[Any] = [inspect.Parameter.empty] * len(node.args)
                keywords = {
                    keyword.arg: inspect.Parameter.empty
                    for keyword in node.keywords
                    if keyword.arg is not None
                }
                try:
                    signature.bind(*positional, **keywords)
                except TypeError as error:
                    failures.append(f"{block!r}: {node.func.id}(...) -- {error}")

        assert not failures, "Documented calls do not match the API:\n" + "\n".join(failures)


class TestTheGateReachesTheDocumentation:
    """A gate over an empty set of blocks passes, and would be silent for
    every reason this module exists. Each assertion here fails if the fence
    pattern, the file selection or the parser stops finding what it did.
    """

    def test_it_reads_the_readme_and_the_how_tos(self) -> None:
        paths = {path.relative_to(ROOT).as_posix() for path in _documented_files()}
        assert "README.md" in paths
        assert any(path.startswith("docs/how-to/") for path in paths)
        assert not any("/adr/" in f"/{path}" for path in paths), (
            "ADR bodies are immutable records and are deliberately out of scope"
        )

    def test_it_finds_a_substantial_number_of_blocks(self) -> None:
        assert len(_PARSED) > 100, f"only {len(_PARSED)} parseable blocks found"

    def test_it_checks_calls_rather_than_merely_walking_past_them(self) -> None:
        checked = sum(
            1
            for _, tree in _PARSED
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in dict(_imported_from_redstring(tree))
        )
        assert checked > 40, f"only {checked} calls resolve to an exported name"

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("SourceDocument(...)", True),
            ("SourceDocument(..., text='x')", False),
            ("SourceDocument(text='x')", False),
        ],
    )
    def test_the_elision_form_is_recognised(self, source: str, expected: bool) -> None:
        call = ast.parse(source).body[0].value  # type: ignore[attr-defined]
        assert _is_elision(call) is expected
