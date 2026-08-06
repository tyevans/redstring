"""`scripts/mutation.py` refuses, and the refusal is what is tested.

A guard that has never been seen to fire is indistinguishable from one that
cannot fire — the reasoning `exhaustive = true` on the import contract was
turned on for, applied to a script. So these tests are about the *refusals*,
not the happy path: the script exists because slice 7 read a perfect score off
a broken environment, and the only line that would have stopped it is the one
that says no.

The decision is a pure function of `(returncode, output)` for exactly this
reason. Testing it by running a real mutation session would take hours and
would not reach the interesting case at all.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mutation.py"
_spec = importlib.util.spec_from_file_location("_mutation_wrapper", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
mutation = importlib.util.module_from_spec(_spec)
sys.modules["_mutation_wrapper"] = mutation
_spec.loader.exec_module(mutation)


class TestItRefusesABaselineItCannotTrust:
    def test_a_failing_suite_is_refused(self):
        assert mutation.baseline_verdict(1, "1 failed, 9 passed in 2s") is not None

    def test_a_zero_exit_with_no_summary_is_refused(self):
        """The incident, exactly.

        A collection error exits non-zero, but the shape being guarded against
        is broader: anything that ends without pytest reporting a result did
        not run the suite, and a mutation session over it reports every mutant
        killed. This is the case a naive `returncode == 0` check lets through.
        """
        verdict = mutation.baseline_verdict(0, "ERROR collecting tests/unit\n")

        assert verdict is not None
        assert "nothing" in verdict

    def test_a_green_run_of_zero_tests_is_refused(self):
        """ "0 failed" and "0 collected" are the same exit code."""
        verdict = mutation.baseline_verdict(0, "0 passed in 0.1s")

        assert verdict is not None
        assert "0 tests" in verdict

    def test_a_real_green_run_is_accepted(self):
        """The other direction, so the guard is not simply refusing always --
        which every test above would still pass against."""
        assert mutation.baseline_verdict(0, "1995 passed in 332.88s") is None


class TestReadingPytestsSummary:
    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("1995 passed in 332.88s", 1995),
            ("1 failed, 9 passed in 2s", 9),
            ("12 passed, 3 skipped, 249 deselected in 16s", 12),
            ("1 passed in 0.1s", 1),
            ("0 passed in 0.1s", 0),
        ],
    )
    def test_it_reads_the_count(self, output: str, expected: int):
        assert mutation.passed_count(output) == expected

    @pytest.mark.parametrize(
        "output",
        [
            "",
            "ERROR collecting tests/unit",
            "ImportError while importing test module",
            "no tests ran in 0.01s",
        ],
    )
    def test_no_summary_is_none_rather_than_zero(self, output: str):
        """`None` and `0` mean different things and the caller acts on both.

        Conflating them would make "the suite could not start" report as "the
        suite ran and passed nothing", which is a worse message for the same
        refusal — and if the default went the other way it would be a *silent*
        acceptance.
        """
        assert mutation.passed_count(output) is None

    def test_the_last_summary_wins(self):
        """pytest can print more than one summary-looking line; the run's own
        verdict is the final one."""
        assert mutation.passed_count("100 passed in 1s\n\n7 passed in 2s") == 7


class TestTheBaselineCommandComesFromTheToolsOwnConfig:
    """Restating the command here would let the two drift, and a baseline
    running a different command than the mutants is not a baseline for them."""

    def test_cosmic_ray_uses_the_configured_test_command(self):
        command = mutation.baseline_command("cosmic-ray")

        assert command[:2] == ["uv", "run"]
        assert "pytest" in command

    def test_mutmut_uses_its_runner_and_test_directories(self):
        command = mutation.baseline_command("mutmut")

        assert "pytest" in command
        assert any(part.startswith("tests/unit") for part in command)

    def test_the_two_tools_do_not_silently_share_one_command(self):
        """Both are configured separately and both are wrapped; a wrapper that
        ran cosmic-ray's command for a mutmut session would be checking the
        wrong environment while looking correct."""
        cosmic = mutation.baseline_command("cosmic-ray")
        mut = mutation.baseline_command("mutmut")

        assert cosmic != mut
