"""Where the library is allowed to ask what time it is.

`observed_at` is *record* time: when this library was told something. It is
threaded down from `composition` as a required argument rather than read where
it is used, for the same reason `reference_date` is -- a clock inside the
extraction path would make a re-extraction of one document stamp its entities
differently on every run, so the same input would stop producing the same
`DocumentExtracted` in a durable, replayable log. That is not a property any
assertion about a single run can see, which is why it is a test about the
source.

A grep in one test module (`"datetime.now" not in inspect.getsource(mapping)`)
was the brief's suggestion and would have been the §3 shape: it passes today,
it passes if someone adds a clock to `pipeline.py` or `merging.py` instead,
and nothing says so. So the check is over the whole package with a named,
reasoned exemption list -- and the list is checked in both directions, per
`docs/adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md`.
"""

from __future__ import annotations

import ast
import pathlib

import redstring

PACKAGE = pathlib.Path(redstring.__file__).parent

#: The one place permitted to read the clock, and why.
#:
#: `composition` is the top layer and the only one holding a caller's request
#: rather than a piece of one. `build_graph` takes `observed_at` and falls back
#: to `datetime.now(UTC)`, so a caller wanting determinism passes a value and
#: everything below it is a pure function of its arguments.
CLOCK_IS_THE_POINT = {
    "composition/build_graph.py": (
        "The vantage point enters the library here. Everything below takes it "
        "as a required argument."
    ),
    # These are not about record time at all: a circuit breaker's recovery
    # timeout and a rate limiter's window are questions about *now* by
    # definition, and no caller could supply an instant that made them
    # meaningful. They are listed rather than excluded by directory so that a
    # clock appearing in, say, `llm/adapters/` still fails.
    "llm/circuit_breaker.py": "Recovery timeout is elapsed wall-clock time by definition.",
    "llm/rate_limiter.py": "A rate-limit window is elapsed wall-clock time by definition.",
}


#: The spellings that ask the operating system what time it is, as
#: `(owner, method)`. `time.time` and `date.today` are named alongside
#: `datetime.now` so the check cannot be satisfied by reaching for a synonym.
CLOCK_CALLS = {("datetime", "now"), ("time", "time"), ("date", "today"), ("datetime", "utcnow")}


def _owner(node: ast.expr) -> str | None:
    """The name the call is made *on*, however it was reached.

    `datetime.now()`, `dt.datetime.now()` and
    `some.deeply.nested.datetime.now()` all answer `"datetime"`. Matching only
    a bare `ast.Name` is what the first version did, and it is why this
    function exists -- see the docstring below.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def modules_reading_the_clock() -> set[str]:
    """Every module under `src/redstring/` that *calls* a clock.

    Two things were learned building this, each by watching it fail:

    - **Over the AST, not the text.** The first version matched substrings and
      three modules failed it on **prose**: `ports/cache.py` explains why an
      adapter must not call `time.time()` itself, and the search read that
      sentence as the thing it forbids. A check that cannot tell a docstring
      from a call is satisfied by deleting the paragraph warning about the
      defect.
    - **The owner is resolved through an attribute chain.** The AST version
      matched `datetime.now()` only when `datetime` was a bare name, so a
      clock written `import datetime as _d; _d.datetime.now(_d.UTC)` passed --
      which is what a deliberate break actually inserted, and the gate said
      nothing. A rule that only catches the tidy spelling catches nobody who
      is working around it.
    """
    found = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and (_owner(node.func.value), node.func.attr) in CLOCK_CALLS
            ):
                found.add(path.relative_to(PACKAGE).as_posix())
    return found


def test_nothing_below_composition_reads_a_clock() -> None:
    assert modules_reading_the_clock() <= set(CLOCK_IS_THE_POINT)


def test_the_detector_finds_something() -> None:
    """A checker over an empty set passes vacuously and is indistinguishable
    from a working one -- the same reasoning as `exhaustive = true` on the
    import contract."""
    assert modules_reading_the_clock()


def test_every_exemption_still_names_a_module_that_reads_a_clock() -> None:
    """The other direction. An entry for a module that has stopped reading a
    clock is an exemption matching nothing, which passes forever while
    describing a tree that no longer exists."""
    assert set(CLOCK_IS_THE_POINT) <= modules_reading_the_clock()


def test_every_exemption_carries_a_reason() -> None:
    assert all(reason.strip() for reason in CLOCK_IS_THE_POINT.values())
