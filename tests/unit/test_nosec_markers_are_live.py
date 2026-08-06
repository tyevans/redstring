"""Every `# nosec` marker must sit on a line bandit would otherwise report.

A suppression is an exemption list one line long, and it has the failure mode
CLAUDE.md describes for all of them: nothing fails when it stops applying.
Move the code, rewrite the statement, delete the risky call entirely -- the
marker stays, silently suppressing whatever lands on that line next. A marker
that has outlived its cause is worse than no marker, because it reads in
review as a considered decision.

**Bandit has a warning for this and it cannot be used.** It prints
`nosec encountered (B608), but no failed test on file ...:365`, which sounds
exactly like the check this module performs. Measured, it is not:

- It **does not affect the exit code.** A run with a genuinely stale marker
  exits 0 unless something *else* fails, so CI stays green.
- It **fires constantly for correct markers.** Bandit attributes a `nosec` to
  the whole statement range, so a suppression on a multi-line call warns for
  every line in that call with no finding on it. The five sound `B608`
  markers in `pgvector.py` produce warnings naming lines 359 and 365, which
  hold no marker at all.

So the signal is both non-blocking and mostly false, which is the worst
combination: too noisy to read, too quiet to gate. This module answers the
question directly instead, by comparing marker lines to findings from a run
with suppression handling switched off.

There are six, and they were added together after a rename touched every file
in `src/` and surfaced eight pre-existing findings that the per-file
pre-commit hook had never scanned:

- five `B608` in `PgVectorStore`, where the only value interpolated into a
  SQL statement is `self._table`, proved a bare identifier by `_IDENTIFIER`
  in `__init__`, with every caller-supplied value travelling as a `$n`
  parameter;
- one `B311` in `llm/retry.py`, where `random` is used for retry jitter and
  there is no secret to predict.

The B608 five have a second guard already, by luck rather than design:
`test_a_table_name_that_is_not_a_bare_identifier_is_rejected` includes a
`"; DROP TABLE users; --` case, so deleting `_IDENTIFIER` fails a test rather
than merely invalidating a comment. Nothing equivalent guarded B311, and
nothing at all checked that any of the six still sat where bandit would look.

This module is that check. It runs bandit with `--ignore-nosec`, which
disables suppression handling entirely, and requires every marker to
correspond to a real finding -- and every finding to have a marker.

Bandit over `src/` takes under half a second, so this stays in the unit tier
where the commit gate runs it. The whole point is to fail at the moment
someone moves the code, not at review.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import tokenize
from pathlib import Path
from typing import NamedTuple

import pytest

PROJECT = Path(__file__).resolve().parents[2]
#: Every tree a `# nosec` can live in and be checked by the configured bandit.
#:
#: `scripts/` is here because the pre-commit hook excludes only `^tests/`, so a
#: marker there is subject to exactly the same rot as one in the package --
#: bandit stops reporting at that line, the suppression stays, and nothing
#: says so. Scanning only `src/` would have made this module's own guarantee
#: quietly partial the first time a script needed one, which it now does.
SCANNED = (PROJECT / "src" / "redstring", PROJECT / "scripts")

#: A real marker is a comment that *starts* `# nosec`. Prose mentioning the
#: convention -- including the block in `pgvector.py` explaining why the five
#: below are sound -- is a `#:` comment with the token in backticks, and must
#: not be mistaken for a suppression. Grep cannot tell those apart; the
#: tokenizer can.
_MARKER = re.compile(r"^#\s*nosec\b(?P<tests>[\w\s,]*)")


class Marker(NamedTuple):
    path: Path
    line: int
    tests: frozenset[str]

    def __repr__(self) -> str:  # pragma: no cover - failure messages only
        ids = ",".join(sorted(self.tests)) or "<no test id>"
        return f"{self.path.relative_to(PROJECT)}:{self.line} ({ids})"


def _markers() -> list[Marker]:
    found: list[Marker] = []
    paths = sorted(path for tree in SCANNED for path in tree.rglob("*.py"))
    for path in paths:
        source = path.read_text()
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            match = _MARKER.match(token.string)
            if match is None:
                continue
            ids = frozenset(t for t in match.group("tests").replace(",", " ").split() if t)
            found.append(Marker(path, token.start[0], ids))
    return found


class Finding(NamedTuple):
    path: Path
    line: int
    test_id: str

    def __repr__(self) -> str:  # pragma: no cover - failure messages only
        return f"{self.path.relative_to(PROJECT)}:{self.line} ({self.test_id})"


def _findings_ignoring_suppressions() -> list[Finding]:
    """Run the configured bandit, with suppression handling switched off.

    `--ignore-nosec` is what makes this measurement mean anything. Running
    bandit normally would report nothing at the suppressed lines by
    construction -- the same trap as measuring a ruff exemption through the
    exemption, which this project has paid for once already.
    """
    completed = subprocess.run(
        [
            "uv",
            "run",
            "bandit",
            "-c",
            "pyproject.toml",
            "-r",
            "src/",
            "scripts/",
            "--ignore-nosec",
            "-f",
            "json",
            "-q",
        ],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )
    # Exit status is 1 whenever there are findings, which is the expected case.
    if not completed.stdout.strip():
        pytest.fail(f"bandit produced no JSON:\n{completed.stdout}\n{completed.stderr}")
    report = json.loads(completed.stdout)
    return [
        Finding(Path(result["filename"]).resolve(), result["line_number"], result["test_id"])
        for result in report["results"]
    ]


@pytest.fixture(scope="module")
def findings() -> list[Finding]:
    return _findings_ignoring_suppressions()


def test_the_detector_finds_the_markers():
    """A checker over an empty set passes vacuously and looks exactly like a
    working one. If the tokenizer stops finding markers -- a rename, a moved
    package root -- every assertion below would silently pass."""
    markers = _markers()
    assert len(markers) >= 6, (
        f"expected at least the six known suppressions, found {markers}. "
        f"If they were genuinely removed, lower this number deliberately."
    )


def test_bandit_reports_something_with_suppressions_disabled(findings: list[Finding]):
    """Guards the other half: a bandit invocation that silently scanned
    nothing would make every marker look stale, or nothing look stale,
    depending on which direction the assertion ran."""
    assert findings, (
        "bandit reported no findings at all with --ignore-nosec, which cannot "
        "be right while any # nosec marker exists. The invocation is probably "
        "scanning the wrong path."
    )


@pytest.mark.parametrize("marker", _markers(), ids=repr)
def test_every_marker_suppresses_a_real_finding(marker: Marker, findings: list[Finding]):
    """The stale-marker case: the risky code moved or went away, and the
    suppression stayed behind to cover whatever arrives next."""
    at_line = [f for f in findings if f.path == marker.path.resolve() and f.line == marker.line]
    assert at_line, (
        f"{marker!r} suppresses nothing -- bandit reports no finding on that line "
        f"even with --ignore-nosec. Delete the marker, or move it to the line that "
        f"needs it."
    )


@pytest.mark.parametrize("marker", _markers(), ids=repr)
def test_every_marker_names_the_check_it_silences(marker: Marker, findings: list[Finding]):
    """A bare `# nosec` silences *every* check on its line, including ones
    nobody considered. Naming the test id keeps the suppression as narrow as
    the argument that justified it."""
    assert marker.tests, (
        f"{marker!r} is a bare `# nosec` and silences every check on that line. "
        f"Name the specific one, e.g. `# nosec B608`."
    )
    here = marker.path.resolve()
    reported = {f.test_id for f in findings if f.path == here and f.line == marker.line}
    unmatched = marker.tests - reported
    assert not unmatched, (
        f"{marker!r} names {sorted(unmatched)}, but bandit reports {sorted(reported)} "
        f"on that line. The suppression no longer describes the finding."
    )


def test_no_finding_is_silenced_without_a_marker(findings: list[Finding]):
    """The inverse direction, and the one that catches a suppression added
    somewhere this module cannot see -- `[tool.bandit] skips` in
    `pyproject.toml`, for instance, which would silence a whole check class
    repository-wide with nothing on the line to show for it."""
    marked = {(m.path.resolve(), m.line) for m in _markers()}
    unmarked = [f for f in findings if (f.path, f.line) not in marked]
    assert not unmarked, (
        f"bandit findings with no `# nosec` on the line: {unmarked}. Either fix them, "
        f"or suppress each one where it happens with the reasoning inline."
    )
