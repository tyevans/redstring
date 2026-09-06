"""`scripts/coverage_ratchet.py --check-rise`: the half of the ratchet CI runs.

The floor -- coverage may not fall -- is enforced by `pytest --cov-fail-under`
and needs nothing here. This mode enforces the other direction: the baseline
must not go *stale* while coverage climbs, because a CI job cannot stage a
file into a commit that already exists, and a floor nobody moves is a check
nobody ever sees fail.

Loaded the way `tests/unit/test_benchmark_script.py` loads its script --
`scripts/` is not a package, so the module comes from its file path rather
than by dotted name.

`measure` and `read_baseline` are patched rather than driven through a real
`.coverage`. What is under test is the comparison and its exit code; building
a coverage database to exercise three inequalities would test `coverage`
rather than this script, and would make the boundary cases below impossible
to hit exactly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "coverage_ratchet.py"
_spec = importlib.util.spec_from_file_location("_coverage_ratchet_script", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
ratchet = importlib.util.module_from_spec(_spec)
sys.modules["_coverage_ratchet_script"] = ratchet
_spec.loader.exec_module(ratchet)


@pytest.fixture
def measuring(monkeypatch: pytest.MonkeyPatch) -> Callable[[float | None, float], None]:
    """Pin what the baseline file says and what the run measured."""

    def pin(baseline: float | None, total: float) -> None:
        monkeypatch.setattr(ratchet, "read_baseline", lambda: baseline)
        monkeypatch.setattr(ratchet, "measure", lambda **_: total)

    return pin


class TestCheckRise:
    def test_a_stale_baseline_fails(self, measuring: Callable[[float | None, float], None]) -> None:
        """The case the mode exists for, and the one that was live when it
        landed: CI measured 96.36% against a file reading 96.22%."""
        measuring(96.22, 96.36)

        assert ratchet.check_rise() == 1

    def test_a_current_baseline_passes(
        self, measuring: Callable[[float | None, float], None]
    ) -> None:
        measuring(96.34, 96.34)

        assert ratchet.check_rise() == 0

    def test_a_fall_is_not_this_gate_s_business(
        self, measuring: Callable[[float | None, float], None]
    ) -> None:
        """Coverage *below* the baseline passes here.

        Not an oversight worth 'fixing': `--cov-fail-under` has already
        failed the job by the time this step runs, and a second opinion on
        the same fact is a second declaration site. Asserted so that anyone
        tempted to add the symmetric check reads this first.
        """
        measuring(96.34, 90.00)

        assert ratchet.check_rise() == 0

    def test_no_baseline_yet_is_silent(
        self, measuring: Callable[[float | None, float], None]
    ) -> None:
        """A fresh checkout has no `.coverage-baseline`; `main` creates it.

        Failing here would make a repository fail a gate about the file it is
        supposed to be generating.
        """
        measuring(None, 99.0)

        assert ratchet.check_rise() == 0

    @pytest.mark.parametrize(
        ("total", "expected", "why"),
        [
            (96.34 + ratchet.TOLERANCE, 0, "exactly at the edge is not over it"),
            (96.34 + ratchet.TOLERANCE + 0.01, 1, "just past the edge is over it"),
        ],
    )
    def test_the_tolerance_boundary_is_pinned_on_both_sides(
        self,
        measuring: Callable[[float | None, float], None],
        total: float,
        expected: int,
        why: str,
    ) -> None:
        """`TOLERANCE` is measurement noise, so the boundary is a real claim.

        Three consecutive runs of one unchanged tree measured 96.30, 96.27 and
        96.30, which is why the constant exists at all. Without a case on each
        side, `>` could be written `>=` -- or the tolerance dropped entirely --
        and every test above would still pass: they all sit far from the edge.
        Pinned as examples rather than sampled, because a boundary is a
        specific value and a property would only sometimes draw it.
        """
        measuring(96.34, total)

        assert ratchet.check_rise() == expected, why
