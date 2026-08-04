"""Test configuration for kg-builder.

Nothing is skipped at collection; `addopts` in pyproject.toml deselects the
`accuracy` and `integration` markers, not this file.

The one thing here is a terminal summary that says *how many* tests that
deselection removed, and how to run them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

#: Markers `addopts` deselects, and the command that runs each.
_DESELECTED_MARKERS = {
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
