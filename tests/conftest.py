"""Test configuration for redstring.

Nothing is skipped at collection; `addopts` in pyproject.toml deselects the
`accuracy` and `integration` markers, not this file.

Two things live here: the hypothesis profile that decides deadline policy for
the whole suite, and a terminal summary saying *how many* tests the
deselection removed and how to run them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import HealthCheck, settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

# ---------------------------------------------------------------------------
# Hypothesis deadline policy -- one declaration site
# ---------------------------------------------------------------------------
#
# **`deadline=None` for the whole suite**, decided here and nowhere else.
#
# This was already the project's position; it was just never written down in
# one place. Nineteen `@settings(...)` decorators carried `deadline=None`
# individually and eight did not, and the difference was not a judgement about
# those eight -- it was whoever wrote them not thinking about it. The residual
# sites were therefore pure flake risk with no compensating signal: a deadline
# enforced on a third of the property suites detects nothing systematically,
# and blocks a commit occasionally.
#
# It duly did. `test_interval.py` failed the commit gate with
# `FlakyFailure: ... Unreliable test timings! On an initial run, this test took
# 276.11ms, which exceeded the deadline of 200.00ms, but on a subsequent run it
# took 1.28 ms`. Two orders of magnitude between the two calls on the same
# input: first-call cost -- imports, strategy construction, page faults --
# landing on a machine that happened to be busy. 1.28 ms is the real number.
#
# Hypothesis reports that as a failure *naming the test*, so the first reading
# is "the interval properties broke" and the second is "flaky, retry". Both are
# wrong, and the gate is `pre-commit`, so the developer meeting it is blocked
# with no obvious cause on a machine loaded by whatever else they were running.
#
# What this gives up, stated plainly: **there is no longer any check that a
# property test has not become pathologically slow.** A per-example wall-clock
# deadline was a poor detector of that anyway -- it cannot distinguish an
# accidentally quadratic `relate` from a busy laptop -- but it was not nothing.
# `redstring/testing/graph_store.py` already made the same trade in the same
# words: "a slow adapter is a performance finding, not a flaky test." A real
# detector is a benchmark with a baseline, which this project does not have.
#
# **Do not put `deadline=` back into a `settings()` decorator.** An explicit
# value there outranks every profile, which would make this block inert for
# that test and unfixable from one place --the same trap `max_examples` carries
# in `redstring/testing/graph_store.py`. `tests/unit/test_hypothesis_deadline_policy.py`
# fails if one reappears.
settings.register_profile("default", deadline=None)

# `--hypothesis-profile=strict` opts back in, for someone deliberately hunting
# a slowdown. Kept because the alternative to a bad detector is usually no
# detector, and this way there is at least a documented way to run one.
settings.register_profile(
    "strict",
    deadline=1000,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile("default")

#: Markers that keep a test out of a run, and the command that runs each.
#:
#: **Order is precedence, because the loop below breaks on the first match and
#: a test may carry two of these.** Every `live` test is also `integration`, so
#: listing `integration` first would report all three `test_live_*.py` modules
#: under a command that does not actually run them without an endpoint. Most
#: specific first; add a new marker above the ones it implies.
_DESELECTED_MARKERS = {
    "live": "uv run pytest -m live    # needs KG_LLM_BASE_URL / KG_EMBED_BASE_URL",
    "integration": "uv run pytest -m integration    # needs docker-compose.test.yml",
    "accuracy": "uv run pytest -m accuracy tests/accuracy/    # needs a live LLM",
}

_deselected: dict[str, int] = {}


def pytest_deselected(items: Sequence[pytest.Item]) -> None:
    """Count what the marker expression removed, grouped by marker."""
    for item in items:
        for marker in _DESELECTED_MARKERS:
            if item.get_closest_marker(marker) is not None:
                _deselected[marker] = _deselected.get(marker, 0) + 1
                break


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Say plainly which tests did not run, and how to run them.

    pytest reports "N deselected" as a bare number with no indication of what
    was skipped or why. That is how an entire adapter comes to be unexecuted
    while the run looks green: slice 4 landed a Neo4j `GraphStore` whose 106
    tests are all `integration`-marked, and a cosmic-ray mutant left in its
    source passed the full default suite because not one line of it ran.

    This cannot make the gate cover that code -- only a combined coverage run
    can, see BACKLOG B10a -- but it stops the omission being silent.
    """
    if not _deselected:
        return
    terminalreporter.write_sep("-", "not run in this invocation")
    for marker, count in sorted(_deselected.items()):
        how = _DESELECTED_MARKERS[marker]
        terminalreporter.write_line(f"  {count:>4} {marker!r} tests -- {how}")
